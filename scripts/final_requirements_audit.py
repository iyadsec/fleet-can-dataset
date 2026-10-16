"""Final Dataset Requirements Audit of the frozen fleetcan-sim-1.1 (READ-ONLY).

Re-checks the 610 frozen sessions against the requirements fixed before generation (docs/final_protocol.md,
milestone specifications) with checks implemented independently of the generator where practical:
  * matrix reconstructed from the protocol table (not from scripts/m6_build_matrix.py)
  * sub-seeds, profile assignment, switch-on offsets and every ECU jitter draw re-derived from the experiment seed
  * CAN frame bit counts recomputed with an independent CRC-15/bit-stuffing implementation (sample)
  * attack parameters, malfunction values (cantools decode), replay links, scenario/campaign structure
  * 24-D descriptors recomputed from raw observation columns only (sampled sessions, every window)
  * Parquet codec / readability / row counts
Nothing under data/ is written. Output: results/final_requirements_audit/audit_results.json (+ CSV tables).

Usage: .venv/bin/python scripts/final_requirements_audit.py [--workers 6]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
FINAL = DATA / "final" / "fleetcan-sim-1.1"
PROV = FINAL / "provenance"
OUT = ROOT / "results" / "final_requirements_audit"
SESSION_DIRS = ("raw", "ground_truth", "metadata", "validation", "analysis")
STEP = {"simultaneous": 0.0, "small": 0.5, "medium": 2.0, "large": 10.0, "partial": 5.0, "probe": 0.1}
FEATURES = (["frame_count", "unique_can_id_count", "can_id_entropy", "most_common_can_id_ratio", "mean_inter_arrival_time",
             "std_inter_arrival_time", "mean_dlc", "std_dlc"] + [f"byte_mean_{i}" for i in range(8)] + [f"byte_std_{i}" for i in range(8)])
FROZEN_ATTACK = {
    "flooding": {"can_id": 0, "rate_hz": 1600, "dlc": 8, "payload": "zeros"},
    "fuzzy": {"mode": "both", "rate_hz": 1000},
    "malfunction": {"mode": "inject", "target": "vehicle_speed", "value": 250.0, "rate_hz": 100},
    "replay": {"window_s": 5.0, "time_offset_ms": 0.25},
}


# ------------------------------------------------------------------ independent re-implementations
def sub_seed(seed: int, tag: str) -> int:
    return int(np.random.SeedSequence([int(seed), zlib.crc32(tag.encode())]).generate_state(1)[0])


def expected_profiles(seed: int, n: int, names=("profile_A", "profile_B", "profile_C")) -> list[str]:
    rng = np.random.default_rng(sub_seed(seed, "profile_assignment"))
    if n < len(names):
        return [names[i] for i in rng.choice(len(names), size=n, replace=False)]
    cyc = [names[i % len(names)] for i in range(n)]
    return [cyc[i] for i in rng.permutation(n)]


def expected_switch_on(seed: int, n: int) -> list[int]:
    draws = np.random.default_rng(sub_seed(seed, "switch_on")).uniform(0.0, 1.0, size=n)
    return [int(round(d * 1e9 / 1000)) * 1000 for d in draws]


def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def can_frame_bits(can_id: int, data: bytes) -> int:
    """Classical CAN data frame, 11-bit ID: stuffing over SOF..CRC, + CRC delim, ACK(2), EOF(7), IFS(3)."""
    bits = [0] + [(can_id >> (10 - i)) & 1 for i in range(11)] + [0, 0, 0] + [(len(data) >> (3 - i)) & 1 for i in range(4)]
    for byte in data:
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    c = crc15(bits)
    bits += [(c >> (14 - i)) & 1 for i in range(15)]
    stuff, run_bit, run = 0, None, 0
    for b in bits:
        if b == run_bit:
            run += 1
        else:
            run_bit, run = b, 1
        if run == 5:
            stuff += 1
            run_bit, run = 1 - b, 1          # the stuff bit starts a new run
    return len(bits) + stuff + 13


def descriptor_features(can_id: np.ndarray, dlc: np.ndarray, payload: list[str], t_ns: np.ndarray) -> np.ndarray:
    n = len(can_id)
    _, counts = np.unique(can_id, return_counts=True)
    p = counts / n
    iat = np.diff(t_ns.astype(np.float64)) / 1e9
    B = np.full((n, 8), np.nan)
    for r, (h, d) in enumerate(zip(payload, dlc)):
        bs = bytes.fromhex(h)
        for i in range(min(int(d), 8)):
            B[r, i] = bs[i]
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bm, bsd = np.nanmean(B, axis=0), np.nanstd(B, axis=0)
    v = np.concatenate([[n, len(counts), float(-(p * np.log2(p + 1e-12)).sum()), counts.max() / n,
                         iat.mean() if len(iat) else np.nan, iat.std() if len(iat) else np.nan,
                         np.mean(dlc), np.std(dlc)], bm, bsd])
    return np.nan_to_num(v, nan=0.0)


# ------------------------------------------------------------------ helpers
def rd(path: Path, columns=None) -> pd.DataFrame:
    return pd.read_parquet(str(path) + ".parquet", columns=columns)


def load_profiles():
    raw = yaml.safe_load((PROV / "profiles_abc.yaml").read_text())["profiles"]
    import cantools
    out = {}
    for name, p in raw.items():
        db = cantools.database.load_file(str(PROV / "dbc" / Path(p["dbc"]).name))
        out[name] = {"msgs": {m["name"]: m for m in p["messages"]}, "roles": p["roles"], "db": db, "bus": p.get("bus", {}),
                     "ids": {m["name"]: db.get_message_by_name(m["name"]).frame_id for m in p["messages"]}}
    return out


def expected_matrix() -> list[tuple]:
    """Configurations reconstructed from docs/final_protocol.md section 4 (independent of the matrix builder)."""
    fam = ["flooding", "fuzzy", "malfunction", "replay"]
    rows = []
    for n in (2, 5, 10):
        rows.append((n, "S0", "none", 0, "none"))
        for f in fam:
            rows.append((n, "S1", f, 1, "isolated"))
            rows.append((n, "S2", f, {2: 2, 5: 3, 10: 5}[n], "independent"))
            for size in (s for s in (2, 5, 10) if s <= n):
                pats = ["simultaneous", "small", "medium", "large", "partial"] if size == n else ["simultaneous", "small"]
                rows += [(n, "S3", f, size, pat) for pat in pats]
            if n >= 5:
                rows.append((n, "S3-mixed", f, {5: 2, 10: 5}[n], "small"))
        rows.append((n, "S3-probe", "replay", 2, "probe"))
    return rows


# ------------------------------------------------------------------ per-session audit
def audit_session(args):
    run, recon = args
    sid, seed, n = run["session_id"], run["seed"], run["fleet_size"]
    P = load_profiles()
    md = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
    val = json.loads((DATA / "validation" / sid / "validation_report.json").read_text())
    dval = json.loads((DATA / "analysis" / sid / "descriptor_validation.json").read_text())
    r = {"session_id": sid, "config_id": run["config_id"], "repetition": run["repetition"], "fleet_size": n,
         "scenario": run["scenario"], "attack": run["attack"], "campaign_size": run["campaign_size"], "pattern": run["pattern"],
         "issues": []}
    iss = r["issues"]

    def need(cond, msg):
        if not cond:
            iss.append(msg)

    # --- versions / seed / fleet
    r["versions"] = md["versions"]
    need(md["versions"] == {"dataset_version": "fleetcan-sim-1.1", "schema_version": "1.0", "generator_version": "1.1.0",
                            "descriptor_version": "1.0"}, f"versions {md['versions']}")
    need(md["random_seed"] == seed, "seed mismatch")
    r["generator_validation"], r["descriptor_validation"] = val["overall"], dval["overall"]
    r["validation_non_pass"] = sorted({c["id"] for c in val["checks"] + dval["checks"] if c["status"] not in ("PASS", "NA")})
    veh = md["fleet"]["vehicles"]
    vids = [v["vehicle_id"] for v in veh]
    need(vids == [f"vehicle_{i:02d}" for i in range(1, n + 1)], "vehicle ids")
    need([v["channel"] for v in veh] == [f"vcan{i}" for i in range(n)], "channel mapping")
    prof = {v["vehicle_id"]: v["profile"] for v in veh}
    r["profiles"] = [v["profile"][-1] for v in veh]
    need([v["profile"] for v in veh] == expected_profiles(seed, n), "profile assignment not reproducible from seed")
    so = {v["vehicle_id"]: int(v["switch_on_ns"]) for v in veh}
    need(list(so.values()) == expected_switch_on(seed, n), "switch-on not reproducible from seed")
    r["switch_on_ns"] = list(so.values())
    asg = md["fleet"].get("assignment", {})
    need(asg.get("profile_assignment", {}).get("strategy") == "balanced_shuffle"
         and asg.get("profile_assignment", {}).get("seed") == sub_seed(seed, "profile_assignment"), "assignment metadata/seed")
    need(asg.get("switch_on", {}).get("range_s") == [0.0, 1.0] and asg.get("switch_on", {}).get("resolution_ns") == 1000
         and asg.get("switch_on", {}).get("seed") == sub_seed(seed, "switch_on"), "switch-on metadata/seed")
    need(asg.get("ecu_jitter", {}).get("fraction") == 0.01 and asg.get("ecu_jitter", {}).get("seed") == sub_seed(seed, "ecu_jitter"), "jitter metadata/seed")
    need(md["simulation"]["step_length_ns"] == 10_000_000 and md["simulation"]["execution"]["mode"] == "fast", "step/mode")

    # --- bus configuration recorded
    need(md["can"]["bus_model"] == "logical" and md["can"]["transport"] == "socketcan", "bus model/transport")
    for pr in set(prof.values()):
        b = P[pr]["bus"]
        need((b.get("stuffing"), b.get("queue_limit"), b.get("drop_policy"), b.get("node_queue_order"), b.get("expiry_policy"), b.get("max_frame_age_ms"))
             == ("exact", 32, "drop_newest", "priority", "deadline", 100.0), f"bus config {pr}")

    # --- frames, labels
    fr = rd(DATA / "raw" / sid / "can_frames")
    lab = rd(DATA / "ground_truth" / sid / "frame_labels")
    for c in ("sim_time_ns", "tx_start_ns", "tx_end_ns", "frame_bits", "dlc", "capture_time_ns"):
        fr[c] = fr[c].astype("int64")
    fr["can_id"] = fr.can_id.map(lambda x: int(x, 0)).astype("int64")
    r["frames"] = len(fr)
    need(fr.frame_uid.is_unique and fr.frame_uid.notna().all(), "frame_uid not unique/non-null")
    need(len(lab) == len(fr) and set(lab.frame_uid) == set(fr.frame_uid), "labels do not cover frames 1:1")
    need(bool(fr.can_id.between(0, 0x7FF).all()), "can_id out of 11-bit range")
    need(bool(fr.dlc.between(0, 8).all()) and bool((fr.payload.str.len() == 2 * fr.dlc).all()), "dlc/payload length")
    fr = fr.merge(lab[["frame_uid", "frame_label", "attack_id", "attack_type", "campaign_id", "message_name", "frame_source"]], on="frame_uid")
    mal = fr.frame_label == "malicious"
    r["malicious_frames"], r["benign_frames"] = int(mal.sum()), int((~mal).sum())
    # bus: one frame at a time, duration = independently recomputed bits x 2 us (sample)
    ov = 0
    for _, g in fr.groupby("vehicle_id"):
        g = g.sort_values("tx_start_ns")
        ov += int((g.tx_start_ns.to_numpy()[1:] < g.tx_end_ns.to_numpy()[:-1]).sum())
    need(ov == 0, f"{ov} overlapping transmissions")
    samp = fr.sample(min(300, len(fr)), random_state=0)
    bits_ok = all(can_frame_bits(int(c), bytes.fromhex(p)) == int(b) and int(e) - int(s) == int(b) * 2000
                  for c, p, b, s, e in zip(samp.can_id, samp.payload, samp.frame_bits, samp.tx_start_ns, samp.tx_end_ns))
    need(bits_ok, "frame bit count / duration mismatch (independent CRC/stuffing)")
    need(bool((fr.tx_start_ns >= fr.sim_time_ns).all()), "transmission before generation")
    # three time bases present and distinct
    r["capture_minus_tx_end_us_p50"] = float(((fr.capture_time_ns - fr.tx_end_ns) / 1e3).median())

    # --- SUMO states and first-frame activation
    st = rd(DATA / "raw" / sid / "vehicle_states", ["sim_time_ns", "vehicle_id", "speed_mps", "accel_mps2"])
    st["sim_time_ns"] = st.sim_time_ns.astype("int64")
    step = np.diff(np.sort(st.sim_time_ns.unique()))
    need(set(step.tolist()) == {10_000_000}, "SUMO step not 10 ms")
    t0 = st.groupby("vehicle_id").sim_time_ns.min()
    need(set(t0.index) == set(vids), "states missing a vehicle")
    r["states_per_vehicle_equal"] = bool(st.groupby("vehicle_id").size().nunique() == 1)
    first = fr[~mal].groupby("vehicle_id").sim_time_ns.min()
    early = {v: int(t0[v] + so[v] - first[v]) for v in vids if first[v] < t0[v] + so[v]}
    r["frames_before_switch_on_vehicles"] = len(early)
    r["max_ns_before_switch_on"] = max(early.values()) if early else 0
    need(all(first[v] >= t0[v] for v in vids), "frame before vehicle exists in SUMO")

    # --- ECU schedule + jitter: re-derive EVERY release time of every benign stream (incl. dropped)
    drops = rd(DATA / "ground_truth" / sid / "dropped_frames")
    gen = pd.concat([fr.loc[~mal, ["vehicle_id", "message_name", "sim_time_ns"]],
                     drops.loc[drops.frame_label == "benign", ["vehicle_id", "message_name", "sim_time_ns"]].astype({"sim_time_ns": "int64"})])
    jseed = sub_seed(seed, "ecu_jitter")
    worst_res, mismatch, streams = 0.0, 0, 0
    for (v, m), g in gen.groupby(["vehicle_id", "message_name"]):
        spec = P[prof[v]]["msgs"][m]
        Pn, off = round(spec["period_ms"] * 1e6), round(spec["offset_ms"] * 1e6)
        t = np.sort(g.sim_time_ns.to_numpy())
        k = np.arange(len(t))
        nominal = int(t0[v]) + so[v] + off + k * Pn
        rng = np.random.default_rng(sub_seed(jseed, f"ecu_jitter/{v}/{m}"))
        rel = nominal + np.round(rng.uniform(-0.01, 0.01, size=len(t)) * Pn).astype(np.int64)
        rel[0] = max(int(t0[v]), rel[0])
        mismatch += int((rel != t).sum())
        worst_res = max(worst_res, float(np.abs(t - nominal).max() / Pn))
        streams += 1
    r["ecu_streams"], r["ecu_release_mismatches"], r["max_abs_jitter_fraction"] = streams, mismatch, worst_res
    need(mismatch == 0, f"{mismatch} ECU release times differ from seed re-derivation")
    need(worst_res <= 0.01 + 1e-9, "jitter beyond +-1 %")

    # --- attacks
    att = rd(DATA / "ground_truth" / sid / "attacks")
    memb = rd(DATA / "ground_truth" / sid / "campaign_membership")
    camps = rd(DATA / "ground_truth" / sid / "campaigns")
    af = rd(DATA / "ground_truth" / sid / "attack_frames")
    rmap = rd(DATA / "ground_truth" / sid / "replay_map")
    r["attacks"], r["campaigns"], r["members"] = len(att), len(camps), len(memb)
    attacked = sorted(set(fr.loc[mal, "vehicle_id"]))
    r["attacked_vehicles"] = attacked
    r["attacked_profiles"] = [prof[v][-1] for v in attacked]
    for a in att.itertuples():
        p = json.loads(a.params) if a.params else {}
        fam = a.attack_type
        need(fam == run["attack"], f"{a.attack_id}: family {fam}")
        need(int(a.attack_seed) == int(np.random.SeedSequence([seed, zlib.crc32(a.attack_id.encode())]).generate_state(1)[0]), f"{a.attack_id}: attack seed derivation")
        dur = (int(a.end_ns) - int(a.start_ns)) / 1e9
        want_dur = 10.0 if run["pattern"] == "partial" else 5.0
        if fam == "replay":
            want_dur += 0.00025                       # replay window end shifted by the frozen +0.25 ms offset
        need(abs(dur - want_dur) < 1e-6, f"{a.attack_id}: duration {dur}")
        fa = fr[fr.attack_id == a.attack_id]
        need(len(fa) == int(a.captured) and int(a.generated) == int(a.transmitted) + int(a.dropped) and int(a.transmitted) == int(a.captured),
             f"{a.attack_id}: counts")
        need(set(fa.vehicle_id) <= {a.vehicle_id}, f"{a.attack_id}: frames on another vehicle")
        need(bool(fa.sim_time_ns.between(int(a.start_ns), int(a.end_ns)).all()), f"{a.attack_id}: frames outside window")
        if fam == "flooding":
            need(int(p.get("can_id", -1)) == 0 and float(p.get("rate_hz")) == 1600 and int(p.get("dlc")) == 8 and p.get("payload") == "zeros", f"{a.attack_id}: flooding params {p}")
            need(bool((fa.can_id == 0).all() and (fa.payload == "00" * 8).all()), f"{a.attack_id}: flood frames")
            need(abs(int(a.generated) - 1600 * dur) <= 1, f"{a.attack_id}: flood generated {a.generated}")
        elif fam == "fuzzy":
            need(p.get("mode") == "both" and float(p.get("rate_hz")) == 1000, f"{a.attack_id}: fuzzy params {p}")
            need(abs(int(a.generated) - 1000 * dur) <= 1, f"{a.attack_id}: fuzzy generated")
            n_ = len(fa)
            exp_unique = 2048 * (1 - math.exp(-n_ / 2048))                  # uniform 11-bit IDs
            need(fa.can_id.nunique() >= 0.9 * exp_unique and fa.can_id.between(0, 0x7FF).all(), f"{a.attack_id}: fuzzy IDs not uniform-random")
            need(fa.dlc.nunique() >= 5, f"{a.attack_id}: fuzzy DLC not randomised")
            r["fuzzy_unique_id_ratio"] = min(r.get("fuzzy_unique_id_ratio", 9), fa.can_id.nunique() / exp_unique)
        elif fam == "malfunction":
            pr = prof[a.vehicle_id]
            msg, sig = P[pr]["roles"]["vehicle_speed"]
            man = p.get("manipulation", {})
            need(p.get("mode") == "inject" and float(p.get("rate_hz")) == 100 and float(man.get("value", -1)) == 250.0
                 and p.get("target") in ("vehicle_speed", sig), f"{a.attack_id}: malfunction params {p}")
            need(bool((fa.can_id == P[pr]["ids"][msg]).all()), f"{a.attack_id}: malfunction CAN ID not profile speed message")
            dbm = P[pr]["db"].get_message_by_name(msg)
            vals = [dbm.decode(bytes.fromhex(h), decode_choices=False)[sig] for h in fa.payload.head(50)]
            need(all(abs(x - 250.0) <= dbm.get_signal_by_name(sig).scale for x in vals), f"{a.attack_id}: decoded speed {vals[:3]}")
            r.setdefault("malfunction_targets", []).append(f"{pr[-1]}:{msg}.{sig}=0x{P[pr]['ids'][msg]:03X}")
        elif fam == "replay":
            need(float(p.get("time_offset_ms")) == 0.25 and p.get("source_vehicle") in ("self", a.vehicle_id), f"{a.attack_id}: replay params {p}")
            rm = rmap[rmap.attack_id == a.attack_id]
            need(len(rm) == int(a.generated), f"{a.attack_id}: replay map rows {len(rm)} vs generated {a.generated}")
            need(bool((rm.source_vehicle == a.vehicle_id).all() and (rm.target_vehicle == a.vehicle_id).all()), f"{a.attack_id}: replay not own vehicle")
            src = fr.set_index("frame_uid").reindex(rm.original_frame_uid)
            need(bool(src.frame_label.eq("benign").all() and src.vehicle_id.eq(a.vehicle_id).all()), f"{a.attack_id}: replay source not benign own frames")
            need(bool((src.can_id.to_numpy() == rm.can_id.map(lambda x: int(str(x), 0)).to_numpy()).all() and (src.payload.to_numpy() == rm.replayed_payload.to_numpy()).all()),
                 f"{a.attack_id}: replay ID/payload differ from source")
            if "window_ns" in p:
                ws, we = (int(x) for x in p["window_ns"])
            else:
                ws, we = (round(float(x) * 1e9) for x in p["window_s"])
            want_w = 10.0 if run["pattern"] == "partial" else 5.0
            need(abs((we - ws) / 1e9 - want_w) < 1e-9 and we <= int(a.start_ns), f"{a.attack_id}: source window {ws}-{we}")
            # source chosen by observed bus time (tx_end) in [w0, w1); replay generated at start + (tx_end - w0) + 0.25 ms
            rpl = fr.set_index("frame_uid").reindex(rm.replay_frame_uid)
            need(bool(src.tx_end_ns.between(ws, we - 1).all()), f"{a.attack_id}: source outside configured window")
            want = int(a.start_ns) + (src.tx_end_ns.to_numpy() - ws) + 250_000
            need(bool((rpl.sim_time_ns.to_numpy() == want).all()), f"{a.attack_id}: replay generation time != start + (source tx_end - w0) + 0.25 ms")
            need(bool(rpl.vehicle_id.eq(a.vehicle_id).all() and rpl.frame_label.eq("malicious").all()), f"{a.attack_id}: replay frames")
            shift = (pd.to_numeric(rm.replay_timestamp) - pd.to_numeric(rm.original_transmission_timestamp)).round(9).unique()
            need(len(shift) == 1, f"{a.attack_id}: replay vs source observed-time shift not constant {shift[:3]}")
            r["replay_links_checked"] = r.get("replay_links_checked", 0) + len(rm)
    # benign vehicles carry no malicious frames; S-structure
    sc = run["scenario"]
    coordinated = camps[camps.coordinated.astype(str).str.lower() == "true"] if len(camps) else camps
    independent = camps[camps.coordinated.astype(str).str.lower() == "false"] if len(camps) else camps
    if sc == "S0":
        need(r["malicious_frames"] == 0 and len(att) == 0 and len(camps) == 0 and len(memb) == 0, "S0 not benign")
    elif sc == "S1":
        need(attacked == ["vehicle_01"] and len(att) == 1 and len(camps) == 0, f"S1 structure {attacked}")
    elif sc == "S2":
        k = run["campaign_size"]
        need(attacked == [f"vehicle_{i:02d}" for i in range(1, k + 1)] and len(att) == k and len(independent) == k and len(coordinated) == 0
             and memb.campaign_id.nunique() == k and memb.groupby("campaign_id").size().eq(1).all(), f"S2 structure {attacked}")
        starts = sorted(memb.vehicle_attack_start_ns.astype("int64") / 1e9)
        r["s2_start_spread_s"] = starts[-1] - starts[0]
    elif sc in ("S3", "S3-probe", "S3-mixed"):
        size = run["campaign_size"]
        stepv = STEP["small" if sc == "S3-mixed" else run["pattern"]]
        need(len(coordinated) == 1, "S3: not exactly one coordinated campaign")
        cid = coordinated.campaign_id.iloc[0] if len(coordinated) else None
        mm = memb[memb.campaign_id == cid].sort_values("vehicle_id")
        need(list(mm.vehicle_id) == [f"vehicle_{i:02d}" for i in range(1, size + 1)], f"S3 members {list(mm.vehicle_id)}")
        need(list(mm.relative_delay_ns.astype("int64")) == [round(stepv * i * 1e9) for i in range(size)], "S3 relative delays")
        need(set(mm.attack_id) == set(att[att.campaign_id == cid].attack_id), "S3 member attack ids")
        need(int(coordinated.vehicle_count.iloc[0]) == size if len(coordinated) else False, "S3 vehicle_count")
        if sc == "S3-mixed":
            m = {5: 1, 10: 2}[n]
            need(len(independent) == m and memb[memb.campaign_id.isin(independent.campaign_id)].groupby("campaign_id").size().eq(1).all()
                 and not set(memb[memb.campaign_id.isin(independent.campaign_id)].vehicle_id) & set(mm.vehicle_id), "mixed independent part")
            need(len(attacked) == size + m, "mixed attacked count")
        else:
            need(len(independent) == 0 and attacked == list(mm.vehicle_id), "S3 extra attacks")
        r["configured_delays_s"] = sorted({round(x / 1e9, 4) for x in mm.relative_delay_ns.astype("int64")})
    # labels -> GT consistency
    ben_ids = fr.loc[~mal, "attack_id"]
    need(bool((ben_ids.isna() | ben_ids.isin(["", "None"])).all()), "benign frame with attack_id")
    ben_c = fr.loc[~mal, "campaign_id"]
    need(bool((ben_c.isna() | ben_c.isin(["", "None"])).all()), "benign frame with campaign_id")
    need(set(fr.loc[mal, "attack_id"]) <= set(att.attack_id), "malicious frame without attack GT")

    # --- descriptors
    desc = rd(DATA / "analysis" / sid / "descriptors")
    dl = rd(DATA / "analysis" / sid / "descriptor_labels")
    need(list(desc.columns[-24:]) == FEATURES, "descriptor feature order")
    need(not ({"anomaly_score", "malicious_frame_count", "attack_id", "campaign_id", "scenario_id"} & set(desc.columns)), "GT/score in descriptors")
    need(len(dl) == len(desc) and set(dl.descriptor_id) == set(desc.descriptor_id), "descriptor labels 1:1")
    nper = fr.groupby("vehicle_id").size()
    dper = desc.groupby("vehicle_id").size()
    need(all(dper.get(v, 0) == (nper[v] - 100) // 50 + 1 for v in vids), "window count != floor((n-100)/50)+1 (trailing windows)")
    need(bool((desc.descriptor_timestamp == desc.window_end_time).all() and (desc.descriptor_timestamp_ns == desc.window_end_ns).all()), "timestamp != window end")
    r["descriptors"] = len(desc)
    if recon:   # full independent reconstruction from observation columns only
        obs = fr[["frame_uid", "vehicle_id", "can_id", "dlc", "payload", "tx_end_ns", "frame_label"]]
        err = 0.0
        lab_err = 0
        desc = desc.merge(dl[["descriptor_id", "malicious_frame_count", "window_class"]], on="descriptor_id")
        for v, g in obs.groupby("vehicle_id"):
            g = g.sort_values(["tx_end_ns", "frame_uid"]).reset_index(drop=True)
            dv = desc[desc.vehicle_id == v].copy()
            dv["window_index"] = dv.window_index.astype(int)
            for d in dv.itertuples():
                w = g.iloc[d.window_index * 50: d.window_index * 50 + 100]
                need(w.frame_uid.iloc[0] == d.first_frame_uid and w.frame_uid.iloc[-1] == d.last_frame_uid, f"{d.descriptor_id}: frame span")
                x = descriptor_features(w.can_id.to_numpy(), w.dlc.to_numpy(), list(w.payload), w.tx_end_ns.to_numpy())
                stored = np.array([float(getattr(d, f)) for f in FEATURES])
                err = max(err, float(np.max(np.abs(x - stored) / np.maximum(1.0, np.abs(stored)))))
                k = int((w.frame_label == "malicious").sum())
                cls = "benign_only" if k == 0 else ("malicious_only" if k == 100 else "mixed")
                lab_err += int(k != int(d.malicious_frame_count) or cls != d.window_class)
        r["recon_descriptors"], r["recon_max_rel_error"], r["recon_label_mismatches"] = len(desc), err, lab_err
        need(err < 1e-9 and lab_err == 0, f"descriptor reconstruction err {err} labels {lab_err}")

    # --- parquet codec / readability
    codecs = set()
    nfiles = 0
    for dname in ("raw", "ground_truth", "analysis"):
        for f in (DATA / dname / sid).glob("*.parquet"):
            md_ = pq.ParquetFile(f).metadata
            nfiles += 1
            for i in range(md_.num_row_groups):
                for j in range(md_.num_columns):
                    codecs.add(md_.row_group(i).column(j).compression)
    r["parquet_files"], r["parquet_codecs"] = nfiles, sorted(codecs)
    need(codecs == {"ZSTD"}, f"codecs {codecs}")
    r["max_file_mtime"] = max(f.stat().st_mtime for d in SESSION_DIRS for f in (DATA / d / sid).rglob("*") if f.is_file())
    r["ok"] = not iss
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    spec = json.loads((PROV / "matrix_spec.json").read_text())
    runs = spec["runs"]
    # descriptor reconstruction sample: repetition 1 of 2 configurations per scenario family per fleet size
    by = defaultdict(list)
    for r in runs:
        if r["repetition"] == 1:
            by[(r["fleet_size"], r["scenario"])].append(r["session_id"])
    recon = {s for v in by.values() for s in sorted(v)[:2]}
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(audit_session, [(r, r["session_id"] in recon) for r in runs], chunksize=4))
    df = pd.DataFrame(res)
    out = {"sessions_audited": len(df), "sessions_with_issues": int((~df.ok).sum()),
           "issues": {r.session_id: r.issues for r in df.itertuples() if r.issues}}
    # matrix
    exp = Counter(expected_matrix())
    got = Counter((r["fleet_size"], r["scenario"], r["attack"], r["campaign_size"], r["pattern"]) for r in runs)
    out["matrix"] = {"expected_configurations": len(exp), "spec_configurations": len({r["config_id"] for r in runs}),
                     "expected_equals_spec": {k: v * 5 for k, v in exp.items()} == dict(got),
                     "reps_per_config": dict(Counter(Counter(r["config_id"] for r in runs).values())),
                     "duplicate_config_rep": len(runs) - len({(r["config_id"], r["repetition"]) for r in runs}),
                     "unique_seeds": len({r["seed"] for r in runs})}
    meta_tags = df[["fleet_size", "scenario", "attack", "campaign_size", "pattern"]].apply(tuple, axis=1)
    out["matrix"]["metadata_matches_spec"] = bool(Counter(meta_tags) == got)
    # coverage tables
    df["campaign_size_s3"] = df.campaign_size.where(df.scenario == "S3")
    cov = df[df.scenario == "S3"].groupby(["fleet_size", "campaign_size", "attack"]).size().unstack(fill_value=0)
    cov.to_csv(OUT / "coverage_fleet_campaign_attack_S3.csv")
    pat = df[df.scenario.isin(["S3", "S3-probe"])].groupby(["pattern", "attack", "fleet_size"]).size().unstack(fill_value=0)
    pat.to_csv(OUT / "coverage_pattern_attack_fleet.csv")
    mix = df[df.scenario == "S3-mixed"].groupby(["fleet_size", "attack", "campaign_size", "pattern"]).size()
    out["mixed_coverage"] = {str(k): int(v) for k, v in mix.items()}
    out["s1_coverage"] = {str(k): int(v) for k, v in df[df.scenario == "S1"].groupby(["fleet_size", "attack"]).size().items()}
    out["s1_attacked_profiles"] = dict(Counter(p for ps in df[df.scenario == "S1"].attacked_profiles for p in ps))
    out["s2_start_spread_s"] = {str(k): sorted(set(np.round(v, 4))) for k, v in df[df.scenario == "S2"].groupby("fleet_size").s2_start_spread_s}
    pa = df.explode("attacked_profiles")
    out["profile_attack_attacked_vehicles"] = {f"{a_}/{p}": int(c) for (a_, p), c in pa[pa.attack != "none"].groupby(["attack", "attacked_profiles"]).size().items()}
    out["malfunction_targets"] = dict(Counter(t for ts in df.get("malfunction_targets", pd.Series(dtype=object)).dropna() for t in ts))
    out["delays_by_pattern"] = {p: sorted({tuple(x) for x in g.configured_delays_s.dropna()}, key=len)[-1] for p, g in df[df.scenario.isin(["S3", "S3-probe"])].groupby("pattern")}
    out["n2_pairs_ordered"] = dict(Counter("".join(p) for p in df[df.fleet_size == 2].profiles))
    out["n2_pairs_unordered"] = dict(Counter("+".join(sorted(p)) for p in df[df.fleet_size == 2].profiles))
    out["profile_counts_by_fleet"] = {int(n): dict(Counter("A%dB%dC%d" % (p.count("A"), p.count("B"), p.count("C")) for p in g.profiles)) for n, g in df.groupby("fleet_size")}
    so_all = np.concatenate(df.switch_on_ns.to_numpy())
    out["switch_on"] = {"n": int(len(so_all)), "min_s": float(so_all.min() / 1e9), "max_s": float(so_all.max() / 1e9),
                        "all_multiple_of_1us": bool((so_all % 1000 == 0).all()), "mean_s": float(so_all.mean() / 1e9)}
    out["jitter"] = {"streams": int(df.ecu_streams.sum()), "release_mismatches": int(df.ecu_release_mismatches.sum()),
                     "max_abs_fraction": float(df.max_abs_jitter_fraction.max())}
    out["activation"] = {"vehicles_with_first_frame_before_switch_on": int(df.frames_before_switch_on_vehicles.sum()),
                         "max_ns_before_switch_on": int(df.max_ns_before_switch_on.max()), "vehicle_traces": int(df.fleet_size.sum())}
    out["recon"] = {"sessions": int(df.recon_descriptors.notna().sum()), "descriptors": int(df.recon_descriptors.fillna(0).sum()),
                    "max_rel_error": float(df.recon_max_rel_error.max()), "label_mismatches": int(df.recon_label_mismatches.fillna(0).sum()),
                    "scenarios": dict(Counter(df[df.recon_descriptors.notna()].scenario))}
    out["validation"] = {"generator": dict(Counter(df.generator_validation)), "descriptor": dict(Counter(df.descriptor_validation)),
                         "non_pass_non_na_checks": dict(Counter(c for cs in df.validation_non_pass for c in cs))}
    out["replay_links_checked"] = int(df.replay_links_checked.fillna(0).sum())
    out["parquet"] = {"files": int(df.parquet_files.sum()), "codecs": sorted({c for cs in df.parquet_codecs for c in cs})}
    out["max_file_mtime_utc"] = pd.Timestamp(df.max_file_mtime.max(), unit="s", tz="UTC").isoformat()
    out["totals"] = {"frames": int(df.frames.sum()), "malicious": int(df.malicious_frames.sum()), "benign": int(df.benign_frames.sum()),
                     "descriptors": int(df.descriptors.sum()), "vehicle_traces": int(df.fleet_size.sum()),
                     "fleet_sizes": {int(k): int(v) for k, v in df.fleet_size.value_counts().sort_index().items()},
                     "traces_by_fleet": {int(k): int(v) for k, v in df.groupby("fleet_size").fleet_size.sum().items()},
                     "min_vehicles": int(df.fleet_size.min()), "max_vehicles": int(df.fleet_size.max())}
    out["states_per_vehicle_equal"] = bool(df.states_per_vehicle_equal.all())
    out["capture_minus_tx_end_us_p50_range"] = [float(df.capture_minus_tx_end_us_p50.min()), float(df.capture_minus_tx_end_us_p50.max())]
    df.drop(columns=["switch_on_ns"]).to_csv(OUT / "per_session_audit.csv", index=False)
    (OUT / "audit_results.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
