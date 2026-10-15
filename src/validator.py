"""Automatic dataset validator. Writes validation_report.json and validation_report.md.

Every check returns PASS, FAIL, WARN or NA (not applicable, e.g. attack checks in benign S0).
NA is never counted as a pass. The overall result is FAIL if any check FAILs.

Usage: python -m src.validator --session <session_id> [--data data]
"""

from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path

import cantools
import numpy as np
import pandas as pd

from .bus_model import frame_bits, utilization_bins
from .config import PROJECT_ROOT
from .campaign_scheduler import build_schedule, check_scenario, pair_overlaps
from .ground_truth import (ATTACK_COLUMNS, ATTACK_FRAME_COLUMNS, CAMPAIGN_COLUMNS, DROP_COLUMNS,
                           LABEL_COLUMNS, MEMBERSHIP_COLUMNS, OVERLAP_COLUMNS, REPLAY_COLUMNS)
from .metadata import sha256
from .recorder import FRAME_COLUMNS, STATE_COLUMNS, fmt_time

REQUIRED_METADATA = ["experiment_id", "session_id", "scenario_id", "random_seed",
                     "simulation.sumo_version", "simulation.step_length_ns", "simulation.sim_start_ns",
                     "simulation.sim_end_ns", "software.python", "software.packages", "fleet.vehicle_count",
                     "fleet.profiles", "time_base.master_clock", "config_sha256", "can.bus_model"]
ECU_SOURCES = {"benign_generator", "attack_modification"}     # frames sent by the vehicle's own ECUs
LIT = dict(keep_default_na=False, na_filter=False)


class Report:
    def __init__(self):
        self.checks: list[dict] = []
        self.info: dict = {}

    def add(self, cid, name, status, detail=""):
        self.checks.append({"id": cid, "check": name, "status": status, "detail": str(detail)})

    def ok(self, cid, name, cond, detail_ok="", detail_fail=""):
        self.add(cid, name, "PASS" if cond else "FAIL", detail_ok if cond else detail_fail)

    def na(self, cid, name, why):
        self.add(cid, name, "NA", why)

    @property
    def overall(self):
        return "FAIL" if any(c["status"] == "FAIL" for c in self.checks) else "PASS"


def _get(d, dotted):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def session_paths(data: Path, sid: str) -> dict[str, Path]:
    raw, gt, md = data / "raw" / sid, data / "ground_truth" / sid, data / "metadata" / sid
    return {"frames": raw / "can_frames.csv", "states": raw / "vehicle_states.csv",
            "unmatched": raw / "unmatched_captures.csv", "labels": gt / "frame_labels.csv",
            "drops": gt / "dropped_frames.csv", "attacks": gt / "attacks.csv",
            "attack_frames": gt / "attack_frames.csv", "replay": gt / "replay_map.csv",
            "campaigns": gt / "campaigns.csv", "membership": gt / "campaign_membership.csv",
            "overlap": gt / "campaign_overlap.csv", "metadata": md / "metadata.json",
            "bus_stats": md / "bus_stats.json", "snapshot": md / "config_snapshot",
            "out_dir": data / "validation" / sid}


_DBC: dict[str, cantools.database.can.Database] = {}


def _dbc(rel):
    if rel not in _DBC:
        _DBC[rel] = cantools.database.load_file(str(PROJECT_ROOT / rel))
    return _DBC[rel]


def _q(s):
    s = pd.Series(s, dtype=float)
    if s.empty:
        return {"n": 0}
    return {"n": int(len(s)), "mean": round(float(s.mean()), 1), "std": round(float(s.std()), 1) if len(s) > 1 else 0.0,
            "p50": round(float(np.percentile(s, 50)), 1), "p95": round(float(np.percentile(s, 95)), 1),
            "p99": round(float(np.percentile(s, 99)), 1), "min": round(float(s.min()), 1), "max": round(float(s.max()), 1)}


def validate(sid: str, data: Path) -> Report:
    r = Report()
    p = session_paths(data, sid)
    need = ["frames", "states", "unmatched", "labels", "drops", "attacks", "attack_frames", "replay",
            "campaigns", "membership", "overlap", "metadata", "bus_stats"]
    missing = [k for k in need if not p[k].exists()]
    r.ok("V01", "required files exist", not missing, "all present", f"missing: {missing}")
    if missing:
        return r

    meta = json.loads(p["metadata"].read_text())
    bstats = json.loads(p["bus_stats"].read_text())
    f = pd.read_csv(p["frames"], dtype=str, **LIT)
    for c in ("sim_time_ns", "tx_start_ns", "tx_end_ns", "frame_bits", "capture_time_ns", "dlc"):
        f[c] = f[c].astype("int64")
    st = pd.read_csv(p["states"], dtype={"timestamp": str, "vehicle_id": str}, **LIT)
    lab = pd.read_csv(p["labels"], dtype=str, **LIT)
    drops = pd.read_csv(p["drops"], dtype=str, **LIT)
    att = pd.read_csv(p["attacks"], dtype=str, **LIT)
    afr = pd.read_csv(p["attack_frames"], dtype=str, **LIT)
    rep = pd.read_csv(p["replay"], dtype=str, **LIT)
    camp = pd.read_csv(p["campaigns"], dtype=str, **LIT)
    memb = pd.read_csv(p["membership"], dtype=str, **LIT)
    ovl = pd.read_csv(p["overlap"], dtype=str, **LIT)
    unm = pd.read_csv(p["unmatched"], dtype=str, **LIT)
    fleet = {v["vehicle_id"]: v for v in meta["fleet"]["vehicles"]}
    profiles = meta["fleet"]["profiles"]
    step_ns = meta["simulation"]["step_length_ns"]
    attack_cfg = {a["attack_id"]: a for a in meta.get("attack_configuration") or []}

    f = f.merge(lab[["frame_uid", "frame_label", "attack_type", "attack_id", "frame_source", "campaign_id"]],
                on="frame_uid", how="left")
    if len(drops):
        drops["sim_time_ns"] = drops.sim_time_ns.astype("int64")
    ecu = f[f.frame_source.isin(ECU_SOURCES)]
    ecu_drops = drops[drops.frame_source.isin(ECU_SOURCES)] if len(drops) else drops

    def msg_spec(vehicle, can_hex):
        prof = profiles[fleet[vehicle]["profile"]]
        name = _dbc(prof["dbc"]).get_message_by_frame_id(int(can_hex, 16)).name
        return next(m for m in prof["messages"] if m["name"] == name)

    # ======================= structure ==========================================================
    schemas = {"frames": (pd.read_csv(p["frames"], nrows=0).columns, FRAME_COLUMNS),
               "labels": (lab.columns, LABEL_COLUMNS), "drops": (drops.columns, DROP_COLUMNS),
               "attacks": (att.columns, ATTACK_COLUMNS), "attack_frames": (afr.columns, ATTACK_FRAME_COLUMNS),
               "replay_map": (rep.columns, REPLAY_COLUMNS), "campaigns": (camp.columns, CAMPAIGN_COLUMNS),
               "campaign_membership": (memb.columns, MEMBERSHIP_COLUMNS), "campaign_overlap": (ovl.columns, OVERLAP_COLUMNS),
               "states": (st.columns, STATE_COLUMNS)}
    bad_schema = [k for k, (got, want) in schemas.items() if list(got) != want]
    r.ok("V02", "schemas match", not bad_schema, "frames, labels, drops, attacks, attack_frames, replay_map, campaigns, states",
         f"mismatch: {bad_schema}")
    req = [c for c in FRAME_COLUMNS if c not in ("capture_offset_us", "payload")]
    nulls = int((f[req].astype(str) == "").sum().sum()) + int(((f.payload == "") & (f.dlc != 0)).sum())
    r.ok("V03", "no missing values in required frame fields", nulls == 0 and len(f) > 0,
         f"{len(f)} frames, 0 empty", f"{nulls} empty fields / {len(f)} rows")
    r.ok("V04", "session_id and scenario_id match metadata",
         set(f.session_id) == {sid} and set(f.scenario_id) == {meta["scenario_id"]} and set(lab.session_id) == {sid},
         f"session={sid}, scenario={meta['scenario_id']}", "foreign or missing IDs")

    # ======================= vehicles / frame content ===========================================
    seen = set(f.vehicle_id)
    r.ok("V05", "vehicle IDs valid and every configured vehicle present", seen == set(fleet), sorted(seen),
         f"seen={sorted(seen)} configured={sorted(fleet)}")
    bad_ch = {v: sorted(set(g.channel)) for v, g in f.groupby("vehicle_id") if set(g.channel) != {fleet[v]["channel"]}}
    r.ok("V06", "each vehicle captured only on its configured channel", not bad_ch,
         ", ".join(f"{v}->{fleet[v]['channel']}" for v in sorted(fleet)), bad_ch)
    ids = f.can_id.map(lambda s: int(s, 16))
    r.ok("V07", "CAN IDs are valid 11-bit identifiers", bool(ids.between(0, 0x7FF).all()),
         f"{f.can_id.nunique()} distinct IDs in 0x000..0x7FF", "ID outside 0x000..0x7FF")
    hex_ok = f.payload.str.fullmatch(r"[0-9A-F]*").all()
    r.ok("V08", "DLC in 0..8 and equals payload length (Classical CAN)",
         bool(hex_ok and f.dlc.between(0, 8).all() and (f.payload.str.len() == 2 * f.dlc).all()),
         "all rows consistent", "DLC/payload mismatch or invalid hex")

    # ======================= time base ===========================================================
    ts_ok = ((f.timestamp == f.sim_time_ns.map(fmt_time)).all()
             and (f.transmission_timestamp == f.tx_end_ns.map(fmt_time)).all()
             and (st.timestamp == st.sim_time_ns.map(fmt_time)).all())
    lo, hi = meta["simulation"]["sim_start_ns"], meta["simulation"]["sim_end_ns"] + step_ns
    r.ok("V09", "timestamps = SUMO time; generation times inside simulated interval",
         bool(ts_ok and f.sim_time_ns.between(lo, hi).all()), f"generation in [{fmt_time(lo)}, {fmt_time(hi)}] s",
         "timestamp/sim_time mismatch or out of range")

    first_state = st.groupby("vehicle_id").sim_time_ns.min()
    gen_all = pd.concat([ecu[["vehicle_id", "can_id", "sim_time_ns"]], ecu_drops[["vehicle_id", "can_id", "sim_time_ns"]]]) \
        if len(ecu_drops) else ecu[["vehicle_id", "can_id", "sim_time_ns"]]
    align_err, period_viol = [], 0
    for (v, cid), g in gen_all.groupby(["vehicle_id", "can_id"]):
        spec = msg_spec(v, cid)
        if g.sim_time_ns.min() != first_state[v] + round(spec["offset_ms"] * 1e6):
            align_err.append(f"{v}/{cid}")
        period_viol += int((g.sim_time_ns.sort_values().diff().dropna() != round(spec["period_ms"] * 1e6)).sum())
    r.ok("V10", "ECU frames and SUMO states share one timeline (first generation = first SUMO state + offset)",
         not align_err, "all vehicle/message streams aligned", f"misaligned: {align_err}")

    mono_tx = all(g.tx_start_ns.is_monotonic_increasing and g.capture_time_ns.is_monotonic_increasing
                  for _, g in f.groupby("vehicle_id"))
    mono_gen = all(g.sim_time_ns.is_monotonic_increasing for _, g in ecu.groupby(["vehicle_id", "can_id"]))
    r.ok("V11", "timestamps monotonic: per vehicle in transmission+capture order; per ECU stream in generation",
         mono_tx and mono_gen, "monotonic", f"tx/capture monotonic={mono_tx}, per-stream generation={mono_gen}")
    r.ok("V12", "every ECU message stream (transmitted + dropped) follows its configured period exactly",
         period_viol == 0, "0 gaps or extra frames", f"{period_viol} deviating intervals")

    dup_uid = int(f.frame_uid.duplicated().sum())
    dup_key = int(f.duplicated(["vehicle_id", "sim_time_ns", "can_id", "frame_source", "payload"]).sum())
    r.ok("V13", "no duplicate records", dup_uid == 0 and dup_key == 0, "0 duplicates",
         f"dup uid={dup_uid}, dup (vehicle,time,id,source,payload)={dup_key}")

    # ======================= transport ===========================================================
    t = meta["transport_stats"]["total"]
    clean = t["missing"] == 0 and t["unexpected"] == 0 and t["content_mismatch"] == 0 and t["send_errors"] == 0 and len(unm) == 0
    r.ok("V14", "every bus-transmitted frame captured on vCAN with identical ID+payload; nothing extra",
         clean and t["matched"] == len(f), f"sent={t['sent']} captured={t['captured']} matched={t['matched']}",
         f"{t}, unmatched rows={len(unm)}")
    bt = meta["can"]["bus_totals"]
    acct = all(v["generated"] == v["transmitted"] + v["dropped"] for v in bt.values())
    acct &= sum(v["transmitted"] for v in bt.values()) == t["sent"] + t["send_errors"]
    acct &= sum(v["dropped"] for v in bt.values()) == len(drops)
    r.ok("V15", "frame accounting: generated = transmitted + dropped; transmitted = sent to vCAN",
         acct, f"generated={sum(v['generated'] for v in bt.values())} dropped={len(drops)}", f"{bt} vs drops file={len(drops)}")

    # ======================= bus timing model ====================================================
    logical = meta["can"]["bus_model"] == "logical"
    exp_bits = [frame_bits(int(c, 16), bytes.fromhex(pl), profiles[fleet[v]["profile"]]["bus"]["stuffing"])
                for c, pl, v in zip(f.can_id, f.payload, f.vehicle_id)]
    bit_ns = f.vehicle_id.map(lambda v: 1e9 / profiles[fleet[v]["profile"]]["bus"]["bitrate"])
    dur_ok = (f.frame_bits == exp_bits).all() and (
        (f.tx_end_ns - f.tx_start_ns - (f.frame_bits * bit_ns).round().astype("int64")).abs().max() == 0 if logical
        else (f.tx_end_ns == f.tx_start_ns).all())
    r.ok("B01", "frame duration = exact bit count (from captured ID+payload) x bit time",
         bool(dur_ok), "all frames" if logical else "ideal bus: zero duration", "duration/bit-count mismatch")
    qd = f.tx_start_ns - f.sim_time_ns
    r.ok("B02", "queue delays non-negative and consistent (queue_delay_us = tx_start - generation)",
         bool((qd >= 0).all() and ((f.queue_delay_us.astype(float) * 1e3 - qd).abs() < 1).all()),
         f"min={qd.min() / 1e3:.1f} us, max={qd.max() / 1e3:.1f} us", f"{int((qd < 0).sum())} negative")
    overlap = 0
    for _, g in f.sort_values("tx_start_ns").groupby("vehicle_id"):
        overlap += int((g.tx_start_ns.values[1:] < g.tx_end_ns.values[:-1]).sum()) if logical else 0
    r.ok("B03", "no overlapping transmissions on any vehicle bus", overlap == 0, "bus carries one frame at a time",
         f"{overlap} overlaps")

    arb_viol = 0
    if logical:
        for _, g in f.groupby("vehicle_id"):
            frames = sorted(zip(g.sim_time_ns, g.tx_start_ns, g.can_id.map(lambda s: int(s, 16))), key=lambda x: x[0])
            order = sorted(range(len(frames)), key=lambda i: frames[i][1])
            heap, j = [], 0
            for i in order:
                ready_i, start_i, id_i = frames[i]
                while j < len(frames) and frames[j][0] <= start_i:
                    heapq.heappush(heap, (frames[j][2], frames[j][1]))
                    j += 1
                while heap and heap[0][1] < start_i:
                    heapq.heappop(heap)
                if heap and heap[0][0] < id_i:
                    arb_viol += 1
        r.ok("B04", "arbitration: no frame waited while a lower-ID frame that was ready lost the bus",
             arb_viol == 0, "priority order respected for every transmission", f"{arb_viol} priority inversions")
    else:
        r.na("B04", "arbitration", "ideal bus (bus_model=ideal)")

    util_ok, util_diff = True, 0.0
    for v, g in f.groupby("vehicle_id"):
        ch = fleet[v]["channel"]
        cs = bstats["channels"][ch]
        bins = utilization_bins(list(zip(g.tx_start_ns, g.tx_end_ns)), 0, cs["span_ns"], bstats["bin_ns"])
        util_ok &= all(0.0 <= b <= 1.0 + 1e-12 for b in bins) and len(bins) == len(cs["utilization_bins"])
        util_diff = max(util_diff, max(abs(a - b) for a, b in zip(bins, cs["utilization_bins"])))
    r.ok("B05", "bus utilization within [0, 1] and equal to recomputation from captured frames",
         util_ok and util_diff < 1e-9,
         "; ".join(f"{ch} overall={c['utilization_overall']:.4f} peak={max(c['utilization_bins']):.4f}"
                   for ch, c in bstats["channels"].items()), f"max diff {util_diff}")
    ql_ok = all(max(c["max_queue_len"].values() or [0]) <= c["config"]["queue_limit"] for c in bstats["channels"].values())
    r.ok("B06", "node queues never exceeded the configured limit", ql_ok, "within queue_limit", "queue limit exceeded")
    _expiry_checks(r, f, drops, fleet, profiles, bt)

    # ======================= DBC / counters / SUMO consistency (benign ECU frames) ===================
    dec_err, ctr_err, speed = 0, 0, []
    streams = pd.concat([ecu[["vehicle_id", "can_id", "sim_time_ns", "payload", "frame_source"]],
                         ecu_drops[["vehicle_id", "can_id", "sim_time_ns", "payload", "frame_source"]]]) \
        if len(ecu_drops) else ecu[["vehicle_id", "can_id", "sim_time_ns", "payload", "frame_source"]]
    for (v, cid), g in streams.sort_values("sim_time_ns").groupby(["vehicle_id", "can_id"]):
        msg = _dbc(profiles[fleet[v]["profile"]]["dbc"]).get_message_by_frame_id(int(cid, 16))
        ctr = []
        for t_ns, pl, src in zip(g.sim_time_ns, g.payload, g.frame_source):
            try:
                d = msg.decode(bytes.fromhex(pl), decode_choices=False)
            except Exception:
                dec_err += 1
                continue
            ctr.append(int(d.get("Counter", 0)))
            if msg.name == "VEHICLE_SPEED" and src == "benign_generator":
                speed.append((v, t_ns, d["VehicleSpeed"]))
        ctr_err += int(sum((b - a) % 16 != 1 for a, b in zip(ctr, ctr[1:])))
    r.ok("V16", "every ECU payload decodes with the vehicle DBC; rolling counters continuous (incl. dropped)",
         dec_err == 0 and ctr_err == 0, f"{len(streams)} ECU frames", f"decode errors={dec_err}, counter gaps={ctr_err}")
    if speed:
        ds = pd.DataFrame(speed, columns=["vehicle_id", "sim_time_ns", "can_kmh"]).sort_values("sim_time_ns")
        m = pd.merge_asof(ds, st[["vehicle_id", "sim_time_ns", "speed_mps"]].sort_values("sim_time_ns"),
                          on="sim_time_ns", by="vehicle_id", direction="backward")
        err = (m.can_kmh - m.speed_mps * 3.6).abs()
        tol = 0.01 + 0.0002 * 3.6
        r.ok("V17", "decoded benign VehicleSpeed equals SUMO speed (sample-and-hold)", bool((err <= tol).all()),
             f"max |error| = {err.max():.4f} km/h over {len(m)} frames", f"{int((err > tol).sum())} frames exceed {tol:.4f}")

    # ======================= labels / ground truth ================================================
    r.ok("V18", "every frame has exactly one ground-truth label row (and vice versa)",
         set(lab.frame_uid) == set(f.frame_uid) and not lab.frame_uid.duplicated().any(), f"{len(lab)} label rows",
         "label/frame sets differ")
    consistent = (((f.frame_source == "benign_generator") & (f.frame_label == "benign") & (f.attack_id == "None"))
                  | (f.frame_source.isin(["attack_injection", "attack_modification"]) & (f.frame_label == "malicious")
                     & (f.attack_id != "None"))).all()
    r.ok("V19", "labels follow provenance: no benign frame labelled malicious and vice versa", bool(consistent),
         f"benign={int((f.frame_label == 'benign').sum())}, malicious={int((f.frame_label == 'malicious').sum())}",
         "label/provenance mismatch")
    att_camp = dict(zip(att.attack_id, att.campaign_id))
    chain_bad = int((f[f.frame_label == "benign"].campaign_id != "None").sum())
    mal_f = f[f.frame_label == "malicious"]
    chain_bad += int((mal_f.campaign_id != mal_f.attack_id.map(att_camp)).sum())
    if len(drops):
        chain_bad += int((drops[drops.frame_label == "benign"].campaign_id != "None").sum())
        md_ = drops[drops.frame_label == "malicious"]
        chain_bad += int((md_.campaign_id != md_.attack_id.map(att_camp)).sum())
    r.ok("V20", "frame -> attack -> campaign chain: benign frames/drops have no campaign; malicious ones carry their attack's campaign",
         chain_bad == 0, f"{len(mal_f)} malicious frames traced", f"{chain_bad} broken links")

    mal = f[f.frame_label == "malicious"]
    if not attack_cfg and mal.empty:
        for cid, name in [("A01", "attack ground truth"), ("A02", "attack windows"), ("A03", "attack counts"),
                          ("A04", "flooding"), ("A05", "fuzzy"), ("A06", "malfunction"), ("A07", "replay")]:
            r.na(cid, name, "no attacks configured")
    else:
        _attack_checks(r, f, drops, att, afr, rep, attack_cfg, fleet, profiles)
    _campaign_checks(r, meta, f, drops, att, camp, memb, ovl, attack_cfg)

    # ======================= reproducibility ======================================================
    absent = [k for k in REQUIRED_METADATA if _get(meta, k) in (None, "", {}, [])]
    seeds_ok = (not attack_cfg) or (att.attack_seed.str.fullmatch(r"\d+").all() and len(att) == len(attack_cfg))
    r.ok("V23", "required metadata present; experiment and per-attack seeds saved", not absent and seeds_ok,
         f"seed={meta.get('random_seed')}, SUMO {meta['simulation']['sumo_version']}", f"missing: {absent}, seeds_ok={seeds_ok}")
    snap_bad = [n for n, h in meta["config_sha256"].items()
                if not (p["snapshot"] / n).exists() or sha256(p["snapshot"] / n) != h]
    r.ok("V24", "configuration snapshot saved and hashes match", not snap_bad, f"{len(meta['config_sha256'])} files", f"bad: {snap_bad}")
    if not _get(meta, "git.commit"):
        r.add("V25", "code version recorded (git commit)", "WARN", "no git commit recorded")
    elif _get(meta, "git.dirty"):
        r.add("V25", "code version recorded (git commit)", "WARN", f"{meta['git']['commit'][:10]} with uncommitted changes")
    else:
        r.add("V25", "code version recorded (git commit)", "PASS", meta["git"]["commit"][:10])

    # ======================= informational statistics ============================================
    r.info["frames_per_vehicle"] = f.groupby("vehicle_id").size().to_dict()
    r.info["frames_by_label"] = {f"{v}/{l}": int(n) for (v, l), n in f.groupby(["vehicle_id", "frame_label"]).size().items()}
    r.info["dropped_by_vehicle_label"] = ({f"{v}/{l}": int(n) for (v, l), n in drops.groupby(["vehicle_id", "frame_label"]).size().items()}
                                          if len(drops) else {})
    r.info["queue_delay_us"] = {v: _q((g.tx_start_ns - g.sim_time_ns) / 1e3)
                                for v, g in f.groupby("vehicle_id")}
    r.info["bus_utilization"] = {ch: {"overall": round(c["utilization_overall"], 4), "peak_100ms": round(max(c["utilization_bins"]), 4)}
                                 for ch, c in bstats["channels"].items()}
    if (f.capture_offset_us != "").all():
        off = f.capture_offset_us.astype(float)
        r.info["capture_offset_us"] = {v: _q(g) for v, g in off.groupby(f.vehicle_id)}
        if max(v["p99"] for v in r.info["capture_offset_us"].values()) > 5000:
            r.add("V26", "capture jitter (p99 capture offset vs modelled transmission end)", "WARN", "> 5 ms; host load or VM scheduling")
        else:
            r.add("V26", "capture jitter (p99 capture offset vs modelled transmission end)", "PASS", "<= 5 ms for all vehicles")
    r.info["attack_windows"] = bstats.get("attack_windows", [])
    return r


def _attack_checks(r, f, drops, att, afr, rep, attack_cfg, fleet, profiles):
    mal = f[f.frame_label == "malicious"]
    ids = set(att.attack_id)
    r.ok("A01", "every malicious frame/drop has an attack_id that exists in attacks.csv; every configured attack present",
         set(mal.attack_id) <= ids and set(drops[drops.frame_label == "malicious"].attack_id) <= ids
         and ids == set(attack_cfg) and set(afr.frame_uid) == set(mal.frame_uid),
         f"{len(ids)} attacks, {len(mal)} malicious frames", "dangling or missing attack ids")

    win_err = []
    for a in att.itertuples():
        c = attack_cfg[a.attack_id]
        start = _start_ns(c)
        if int(a.start_ns) != start:
            win_err.append(f"{a.attack_id} start")
        if "duration_ns" in c or c["type"] != "replay":
            dur = int(c["duration_ns"]) if "duration_ns" in c else round(float(c["duration_s"]) * 1e9)
            if int(a.end_ns) != start + dur:
                win_err.append(f"{a.attack_id} end")
        if a.campaign_id != (c.get("campaign_id") or "None"):
            win_err.append(f"{a.attack_id} campaign")
        gen = pd.concat([mal[mal.attack_id == a.attack_id].sim_time_ns,
                         drops[drops.attack_id == a.attack_id].sim_time_ns]) if len(drops) else mal[mal.attack_id == a.attack_id].sim_time_ns
        if len(gen) and not (gen.min() >= int(a.start_ns) and gen.max() < int(a.end_ns)):
            win_err.append(f"{a.attack_id} frames outside window")
        if f[f.attack_id == a.attack_id].vehicle_id.ne(a.vehicle_id).any():
            win_err.append(f"{a.attack_id} wrong vehicle")
    r.ok("A02", "attack start/end match configuration; every attack frame generated inside its window on its vehicle",
         not win_err, "all windows consistent", win_err)

    cnt_err = []
    for a in att.itertuples():
        n_cap = int((mal.attack_id == a.attack_id).sum())
        n_drop = int((drops.attack_id == a.attack_id).sum()) if len(drops) else 0
        if not (int(a.generated) == int(a.transmitted) + int(a.dropped) and int(a.captured) == n_cap
                and int(a.dropped) == n_drop and int(a.transmitted) == n_cap
                and int(a.queue_dropped) + int(a.expired_dropped) == int(a.dropped)):
            cnt_err.append(f"{a.attack_id}: gen={a.generated} tx={a.transmitted} drop={a.dropped}/{n_drop} cap={a.captured}/{n_cap}")
    r.ok("A03", "per attack: generated = transmitted + dropped; transmitted = captured; matches frame files",
         not cnt_err, "; ".join(f"{a.attack_id}: gen={a.generated} tx={a.transmitted} drop={a.dropped}" for a in att.itertuples()),
         cnt_err)

    def frames_of(kind):
        return {a: c for a, c in attack_cfg.items() if c["type"] == kind}

    fl = frames_of("flooding")
    if fl:
        bad = 0
        for aid, c in fl.items():
            p = c["params"]
            g = mal[mal.attack_id == aid]
            want_id = int(p.get("can_id", 0))
            bad += int((g.can_id.map(lambda s: int(s, 16)) != want_id).sum())
            bad += int((g.dlc != int(p.get("dlc", 8))).sum())
            if p.get("payload", "zeros") == "zeros":
                bad += int((g.payload != "00" * int(p.get("dlc", 8))).sum())
            elif p.get("payload") != "random":
                bad += int((g.payload != str(p["payload"]).upper()).sum())
            gen_all = pd.concat([g.sim_time_ns, drops[drops.attack_id == aid].sim_time_ns]) if len(drops) else g.sim_time_ns
            period = round(1e9 / float(p["rate_hz"]))
            bad += int((gen_all.sort_values().diff().dropna() != period).sum())
        r.ok("A04", "flooding frames: configured ID, DLC, payload and exact generation period", bad == 0, f"{len(fl)} attack(s)", f"{bad} violations")
    else:
        r.na("A04", "flooding", "no flooding attack")

    fz = frames_of("fuzzy")
    if fz:
        bad = 0
        for aid, c in fz.items():
            p, g = c["params"], mal[mal.attack_id == aid]
            ids_ = g.can_id.map(lambda s: int(s, 16))
            lo, hi = p.get("id_range", [0, 0x7FF])
            if p["mode"] == "payload":
                bad += int((ids_ != int(p["can_id"])).sum())
            else:
                bad += int((~ids_.between(int(lo), int(hi))).sum())
            if p["mode"] == "id":
                dlc = 8 if p.get("dlc", 8) == "random" else int(p.get("dlc", 8))
                bad += int((g.payload != (bytes(dlc).hex().upper() if p.get("payload", "zeros") == "zeros" else str(p["payload"]).upper())).sum())
            if p.get("dlc", 8) != "random":
                bad += int((g.dlc != int(p.get("dlc", 8))).sum())
            bad += int((afr[afr.attack_id == aid].fuzzy_mode != p["mode"]).sum())
        r.ok("A05", "fuzzy frames respect the configured mode (what is randomised, ID range, DLC)", bad == 0,
             ", ".join(f"{a}:{c['params']['mode']}" for a, c in fz.items()), f"{bad} violations")
    else:
        r.na("A05", "fuzzy", "no fuzzy attack")

    mf = frames_of("malfunction")
    if mf:
        bad, n = 0, 0
        for aid, c in mf.items():
            p = c["params"]
            prof = profiles[fleet[c["vehicle"]]["profile"]]
            msg = _dbc(prof["dbc"]).get_message_by_name(p["message"])
            sig = msg.get_signal_by_name(p["signal"])
            g = mal[mal.attack_id == aid].merge(afr[["frame_uid", "expected_value", "malicious_value", "original_payload"]], on="frame_uid")
            s0 = _start_ns(c)
            dur = int(c["duration_ns"]) if "duration_ns" in c else round(float(c["duration_s"]) * 1e9)
            for row in g.itertuples():
                n += 1
                d = msg.decode(bytes.fromhex(row.payload), decode_choices=False)
                got, exp = float(d[p["signal"]]), float(row.expected_value)
                m = p["manipulation"]
                if m["strategy"] == "constant":
                    want = float(m["value"])
                elif m["strategy"] == "offset":
                    want = exp + float(m["offset"])
                elif m["strategy"] == "scale":
                    want = exp * float(m["factor"])
                else:
                    want = float(m["from"]) + (float(m["to"]) - float(m["from"])) * (row.sim_time_ns - s0) / dur
                bad += int(abs(got - want) > sig.scale / 2 + 1e-9 or abs(got - float(row.malicious_value)) > 1e-9)
                bad += int(int(row.can_id, 16) != msg.frame_id)
                if p.get("mode", "inject") == "replace":
                    orig = msg.decode(bytes.fromhex(row.original_payload), decode_choices=False)
                    bad += int(abs(float(orig[p["signal"]]) - exp) > 1e-9)
                    bad += int(any(orig[k] != d[k] for k in orig if k != p["signal"]))
        r.ok("A06", "malfunction frames: target message ID; decoded signal = configured manipulation (within DBC resolution)",
             bad == 0, f"{n} frames checked", f"{bad} violations")
    else:
        r.na("A06", "malfunction", "no malfunction attack")

    if frames_of("replay"):
        bad = 0
        fi = f.set_index("frame_uid")
        rp = rep.copy()
        missing = int((~rp.original_frame_uid.isin(fi.index)).sum())
        ok = rp[rp.original_frame_uid.isin(fi.index)]
        for row in ok.itertuples():
            o, rpl = fi.loc[row.original_frame_uid], fi.loc[row.replay_frame_uid]
            c = attack_cfg[row.attack_id]["params"]
            w0, w1 = _window_ns(c)
            start = _start_ns(attack_cfg[row.attack_id])
            off = round(float(c.get("time_offset_ms", 0)) * 1e6)
            bad += int(o.frame_label != "benign" or o.vehicle_id != row.source_vehicle or rpl.vehicle_id != row.target_vehicle)
            bad += int(o.payload != rpl.payload or o.can_id != rpl.can_id or row.original_payload != row.replayed_payload)
            bad += int(not (w0 <= o.tx_end_ns < w1))
            bad += int(rpl.sim_time_ns != start + (o.tx_end_ns - w0) + off)
        n_replay = int((mal.attack_type == "replay").sum())
        r.ok("A07", "replay frames reference valid benign originals in the window; same ID+payload; exact time shift",
             bad == 0 and missing == 0 and len(rp) == n_replay, f"{len(rp)} replayed frames linked",
             f"{bad} violations, {missing} missing originals, map={len(rp)} vs frames={n_replay}")
    else:
        r.na("A07", "replay", "no replay attack")


def _start_ns(c: dict) -> int:
    return int(c["start_ns"]) if "start_ns" in c else round(float(c["start_s"]) * 1e9)


def _window_ns(p: dict) -> tuple[int, int]:
    if "window_ns" in p:
        return int(p["window_ns"][0]), int(p["window_ns"][1])
    return round(float(p["window_s"][0]) * 1e9), round(float(p["window_s"][1]) * 1e9)


def _expiry_checks(r, f, drops, fleet, profiles, bus_totals):
    bad, n_exp = 0, 0
    pol = {v: profiles[fleet[v]["profile"]]["bus"] for v in fleet}
    qd = f.tx_start_ns - f.sim_time_ns
    for v, cfg in pol.items():
        if cfg.get("expiry_policy", "retain") == "deadline":
            bad += int((qd[f.vehicle_id == v] > round(cfg["max_frame_age_ms"] * 1e6)).sum())
    if len(drops):
        for d in drops.itertuples():
            cfg = pol[d.vehicle_id]
            if d.drop_kind == "expired_drop":
                n_exp += 1
                bad += int(cfg.get("expiry_policy", "retain") != "deadline"
                           or int(d.drop_time_ns) != int(d.sim_time_ns) + round(cfg["max_frame_age_ms"] * 1e6)
                           or not d.reason.startswith("expired"))
            else:
                bad += int(d.drop_kind != "queue_drop" or not d.reason.startswith("queue_full"))
    totals_ok = all(v.get("queue_dropped", 0) + v.get("expired_dropped", 0) == v["dropped"] for v in bus_totals.values())
    policies = sorted({f"{c.get('expiry_policy', 'retain')}"
                       + (f"({c['max_frame_age_ms']} ms)" if c.get("expiry_policy") == "deadline" else "") for c in pol.values()})
    r.ok("B07", "frame expiry: no frame transmitted after its deadline; expired drops at generation + max age; "
                "queue_drop / expired_drop counted separately", bad == 0 and totals_ok,
         f"policy {policies}; expired drops={n_exp}, queue drops={len(drops) - n_exp}", f"{bad} violations, totals_ok={totals_ok}")


def _campaign_checks(r, meta, f, drops, att, camp, memb, ovl, attack_cfg):
    raw = meta.get("campaign_configuration") or []
    sched = build_schedule(raw, meta["random_seed"], list({v["vehicle_id"] for v in meta["fleet"]["vehicles"]}))
    iso = [a for a in attack_cfg.values() if not a.get("campaign_id")]
    probs = check_scenario(meta["scenario_id"], iso, sched)
    r.ok("C00", f"scenario {meta['scenario_id']} consistent with its definition", not probs,
         f"{len(sched)} campaigns ({sum(c.coordinated for c in sched)} coordinated), {len(iso)} isolated attacks", probs)
    if not raw:
        r.na("C01", "campaign checks", "no campaigns configured")
        r.ok("C02", "no campaign ground truth without campaign configuration",
             camp.empty and memb.empty and ovl.empty and set(att.campaign_id) <= {"None"}, "empty", "unexpected campaign rows")
        return
    want = {c.campaign_id: c for c in sched}
    r.ok("C01", "every configured campaign exists exactly once in campaigns.csv (and nothing else)",
         sorted(camp.campaign_id) == sorted(want), sorted(want), f"file={sorted(camp.campaign_id)}")

    # membership: exactly the configured targets, one attack each, attacks.csv agrees
    exp_m = {(m.campaign_id, m.vehicle_id, m.attack_id) for c in sched for m in c.members}
    got_m = set(zip(memb.campaign_id, memb.vehicle_id, memb.attack_id))
    att_m = {(a.campaign_id, a.vehicle_id, a.attack_id) for a in att.itertuples() if a.campaign_id != "None"}
    r.ok("C02", "membership = configured targets; every attack referencing a campaign is a member (no merging, no omission)",
         exp_m == got_m == att_m and not memb.duplicated(["campaign_id", "vehicle_id"]).any(),
         f"{len(exp_m)} memberships", f"config-only={exp_m - got_m}, file-only={got_m - exp_m}, attacks={att_m ^ exp_m}")

    # logical timing exactly as configured (independent of queueing / capture)
    t_err = []
    for c in sched:
        row = camp[camp.campaign_id == c.campaign_id].iloc[0]
        if int(row.campaign_start_ns) != c.start_ns or int(row.campaign_end_ns) != c.end_ns:
            t_err.append(f"{c.campaign_id} campaign start/end")
        if row.coordinated != str(c.coordinated).lower() or int(row.vehicle_count) != len(c.members):
            t_err.append(f"{c.campaign_id} attributes")
        for m in c.members:
            mr = memb[(memb.campaign_id == c.campaign_id) & (memb.vehicle_id == m.vehicle_id)].iloc[0]
            if (int(mr.vehicle_attack_start_ns), int(mr.vehicle_attack_end_ns), int(mr.relative_delay_ns)) != (m.start_ns, m.end_ns, m.relative_delay_ns):
                t_err.append(f"{c.campaign_id}/{m.vehicle_id}")
            a = att[att.attack_id == m.attack_id].iloc[0]
            if (int(a.start_ns), int(a.end_ns)) != (m.start_ns, m.end_ns):
                t_err.append(f"{m.attack_id} attack window")
        cfgc = c.raw
        if c.delay_pattern == "simultaneous" and any(m.relative_delay_ns for m in c.members):
            t_err.append(f"{c.campaign_id} simultaneous but non-zero delay")
        if c.delay_pattern == "fixed" and [m.relative_delay_ns for m in c.members] != [round(float(x) * 1e9) for x in cfgc["relative_delays_s"]]:
            t_err.append(f"{c.campaign_id} fixed delays differ from configuration")
    r.ok("C03", "campaign/member start, end and relative delays equal configuration exactly (ns), simultaneous => 0, fixed => configured",
         not t_err, "all exact", t_err)

    # the injected traffic really starts on the logical timeline at the configured time
    obs_err, layer = [], {}
    gen = pd.concat([f[["attack_id", "sim_time_ns"]], drops[["attack_id", "sim_time_ns"]]]) if len(drops) else f[["attack_id", "sim_time_ns"]]
    gen = gen[gen.attack_id != "None"]
    first_gen = gen.groupby("attack_id").sim_time_ns.min()
    last_gen = gen.groupby("attack_id").sim_time_ns.max()
    mal = f[f.attack_id != "None"]
    first_tx, first_cap = mal.groupby("attack_id").tx_start_ns.min(), mal.groupby("attack_id").capture_time_ns.min()
    for c in sched:
        rows = []
        for m in c.members:
            fg = first_gen.get(m.attack_id)
            if fg is None:
                obs_err.append(f"{m.attack_id} produced no frames")
                continue
            if c.attack_type in ("flooding", "fuzzy") or (c.attack_type == "malfunction" and m.params.get("mode", "inject") == "inject"):
                phase = round(float(m.params.get("phase_ms", 0)) * 1e6)
                if fg != m.start_ns + phase:
                    obs_err.append(f"{m.attack_id} first generation {fg} != start {m.start_ns}+{phase}")
            elif fg < m.start_ns:
                obs_err.append(f"{m.attack_id} generated before start")
            if last_gen[m.attack_id] >= m.end_ns:
                obs_err.append(f"{m.attack_id} generated after end")
            rows.append((m.vehicle_id, m.relative_delay_ns, fg, first_tx.get(m.attack_id), first_cap.get(m.attack_id)))
        if len(rows) > 1:
            g0, t0, c0 = rows[0][2], rows[0][3], rows[0][4]
            layer[c.campaign_id] = [{"vehicle_id": v, "configured_delay_s": rel / 1e9,
                                     "first_generation_delay_s": (g - g0) / 1e9,
                                     "first_transmission_delay_s": None if t is None or t0 is None else (t - t0) / 1e9,
                                     "first_capture_delay_s": None if cp is None or c0 is None else (cp - c0) / 1e9}
                                    for v, rel, g, t, cp in rows]
    r.ok("C04", "observed attack traffic lies inside each member's logical window; grid attacks start exactly at the configured time",
         not obs_err, f"{len(first_gen)} attacks checked", obs_err)
    r.info["campaign_delay_by_layer"] = layer
    # timing-layer independence: ground truth equals configuration even where transmission/capture delays differ
    diffs = [abs(x["first_capture_delay_s"] - x["configured_delay_s"]) for v in layer.values() for x in v
             if x["first_capture_delay_s"] is not None]
    r.ok("C05", "ground-truth delays come from the logical timeline only (unchanged by CAN queueing and capture jitter)",
         not t_err, f"GT delay == configured for all members; capture-layer delays deviate up to "
                    f"{max(diffs) * 1e3 if diffs else 0:.3f} ms and are NOT used", "see C03")

    indep_bad = [c.campaign_id for c in sched if not c.coordinated and len(c.members) != 1]
    shared = att[att.campaign_id != "None"].groupby("campaign_id").vehicle_id.nunique()
    merged = [cid for cid, n in shared.items() if n > 1 and not want[cid].coordinated]
    r.ok("C06", "independent campaigns are single-vehicle and never merged into a shared campaign",
         not indep_bad and not merged, f"{sum(not c.coordinated for c in sched)} independent campaigns",
         f"multi-vehicle independent={indep_bad}, merged={merged}")

    ov_bad = 0
    for c in sched:
        for o in pair_overlaps(c):
            g = ovl[(ovl.campaign_id == c.campaign_id) & (ovl.vehicle_a == o["vehicle_a"]) & (ovl.vehicle_b == o["vehicle_b"])]
            ov_bad += int(len(g) != 1 or int(g.iloc[0].start_delay_ns) != o["start_delay_ns"]
                          or abs(float(g.iloc[0].jaccard) - o["jaccard"]) > 1e-6)
    r.ok("C07", "pairwise start delays and interval overlaps (overlap, overlap/min duration, Jaccard) recomputed from configuration",
         ov_bad == 0 and len(ovl) == sum(len(pair_overlaps(c)) for c in sched), f"{len(ovl)} vehicle pairs", f"{ov_bad} mismatches")

    if len(drops):
        md_ = drops[drops.frame_label == "malicious"]
        ok = (md_.attack_id != "None").all() and md_.attack_id.isin(att.attack_id).all()
        r.ok("C08", "dropped/expired attack frames remain in ground truth with attack_id and campaign_id",
             bool(ok), f"{len(md_)} malicious drops ({int((md_.drop_kind == 'expired_drop').sum())} expired)", "unmapped drops")
    else:
        r.add("C08", "dropped/expired attack frames remain in ground truth", "PASS", "no drops occurred")


def write_report(r: Report, sid: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / "validation_report.json"
    j.write_text(json.dumps({"session_id": sid, "overall": r.overall, "checks": r.checks, "info": r.info}, indent=2, default=str))
    lines = [f"# Validation report: {sid}", "", f"**Overall: {r.overall}**", "",
             "| ID | Check | Status | Detail |", "|---|---|---|---|"]
    lines += [f"| {c['id']} | {c['check']} | {c['status']} | {c['detail']} |" for c in r.checks]
    lines += ["", "## Statistics (informational)", "", "```json", json.dumps(r.info, indent=2, default=str), "```"]
    m = out_dir / "validation_report.md"
    m.write_text("\n".join(lines) + "\n")
    return j, m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", required=True)
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"))
    a = ap.parse_args(argv)
    r = validate(a.session, Path(a.data))
    j, m = write_report(r, a.session, session_paths(Path(a.data), a.session)["out_dir"])
    for c in r.checks:
        print(f"[{c['status']:4s}] {c['id']} {c['check']}: {c['detail']}")
    print(f"OVERALL: {r.overall}\n{j}\n{m}")
    return 0 if r.overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
