"""Automatic dataset validator. Writes validation_report.json and validation_report.md.

Every check returns PASS, FAIL, WARN or NA (not applicable, e.g. attack checks in benign S0).
NA is never counted as a pass. The overall result is FAIL if any check FAILs.

Usage: python -m src.validator --session <session_id> [--data data]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cantools
import numpy as np
import pandas as pd

from .config import PROJECT_ROOT
from .ground_truth import CAMPAIGN_COLUMNS, LABEL_COLUMNS
from .metadata import sha256
from .recorder import FRAME_COLUMNS, STATE_COLUMNS, fmt_time

REQUIRED_METADATA = ["experiment_id", "session_id", "scenario_id", "random_seed",
                     "simulation.sumo_version", "simulation.step_length_ns", "simulation.sim_start_ns",
                     "simulation.sim_end_ns", "software.python", "software.packages", "fleet.vehicle_count",
                     "fleet.profiles", "time_base.master_clock", "config_sha256"]


class Report:
    def __init__(self):
        self.checks: list[dict] = []
        self.info: dict = {}

    def add(self, cid: str, name: str, status: str, detail: str = ""):
        self.checks.append({"id": cid, "check": name, "status": status, "detail": detail})

    def ok(self, cid, name, cond: bool, detail_ok="", detail_fail=""):
        self.add(cid, name, "PASS" if cond else "FAIL", detail_ok if cond else detail_fail)

    @property
    def overall(self) -> str:
        return "FAIL" if any(c["status"] == "FAIL" for c in self.checks) else "PASS"


def _get(d: dict, dotted: str):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def session_paths(data: Path, sid: str) -> dict[str, Path]:
    return {
        "frames": data / "raw" / sid / "can_frames.csv",
        "states": data / "raw" / sid / "vehicle_states.csv",
        "unmatched": data / "raw" / sid / "unmatched_captures.csv",
        "labels": data / "ground_truth" / sid / "frame_labels.csv",
        "campaigns": data / "ground_truth" / sid / "campaigns.csv",
        "metadata": data / "metadata" / sid / "metadata.json",
        "snapshot": data / "metadata" / sid / "config_snapshot",
        "out_dir": data / "validation" / sid,
    }


def validate(sid: str, data: Path) -> Report:
    r = Report()
    p = session_paths(data, sid)

    missing = [k for k in ("frames", "states", "unmatched", "labels", "campaigns", "metadata") if not p[k].exists()]
    r.ok("V01", "required files exist", not missing, "all present", f"missing: {missing}")
    if missing:
        return r

    meta = json.loads(p["metadata"].read_text())
    # keep_default_na=False: strings such as "None" or "" are kept literally, never turned into NaN.
    lit = dict(keep_default_na=False, na_filter=False)
    f = pd.read_csv(p["frames"], dtype={"timestamp": str, "payload": str, "can_id": str, "capture_offset_us": str,
                                        "vehicle_id": str, "channel": str, "session_id": str, "scenario_id": str}, **lit)
    st = pd.read_csv(p["states"], dtype={"timestamp": str}, **lit)
    lab = pd.read_csv(p["labels"], dtype=str, **lit)
    camp = pd.read_csv(p["campaigns"], dtype=str, **lit)
    unm = pd.read_csv(p["unmatched"], dtype=str, **lit)
    fleet = {v["vehicle_id"]: v for v in meta["fleet"]["vehicles"]}
    profiles = meta["fleet"]["profiles"]
    step_ns = meta["simulation"]["step_length_ns"]

    # --- structure -------------------------------------------------------------------------
    cols_ok = (list(f.columns) == FRAME_COLUMNS and list(lab.columns) == LABEL_COLUMNS
               and list(camp.columns) == CAMPAIGN_COLUMNS and list(st.columns) == STATE_COLUMNS)
    r.ok("V02", "schemas match (frames, labels, campaigns, states)", cols_ok, "", "column mismatch")
    req = [c for c in FRAME_COLUMNS if c not in ("capture_offset_us", "payload")]
    nulls = int((f[req].astype(str) == "").sum().sum()) + int(((f.payload == "") & (f.dlc != 0)).sum())
    r.ok("V03", "no missing values in required frame fields", nulls == 0 and len(f) > 0,
         f"{len(f)} frames, 0 nulls", f"{nulls} nulls / {len(f)} rows")
    r.ok("V04", "session_id and scenario_id match metadata",
         set(f.session_id) == {sid} and set(f.scenario_id) == {meta["scenario_id"]} and set(lab.session_id) == {sid},
         f"session={sid}, scenario={meta['scenario_id']}", "foreign or missing IDs")

    # --- vehicles / channels -----------------------------------------------------------------
    seen = set(f.vehicle_id)
    r.ok("V05", "vehicle IDs valid and every configured vehicle present", seen == set(fleet),
         f"{sorted(seen)}", f"seen={sorted(seen)} configured={sorted(fleet)}")
    chan = f.groupby("vehicle_id").channel.unique()
    bad_ch = {v: list(c) for v, c in chan.items() if list(c) != [fleet.get(v, {}).get("channel")]}
    r.ok("V06", "each vehicle captured only on its configured channel", not bad_ch,
         ", ".join(f"{v}->{fleet[v]['channel']}" for v in sorted(fleet)), f"{bad_ch}")

    # --- frame content -------------------------------------------------------------------------
    ids = f.can_id.map(lambda s: int(s, 16))
    r.ok("V07", "CAN IDs are valid 11-bit identifiers", bool(((ids >= 0) & (ids <= 0x7FF)).all()),
         f"IDs: {sorted(f.can_id.unique())}", "ID outside 0x000..0x7FF")
    plen = f.payload.str.len()
    hex_ok = f.payload.str.fullmatch(r"[0-9A-F]*").all()
    r.ok("V08", "DLC in 0..8 and equals payload length (Classical CAN)",
         bool(hex_ok and f.dlc.between(0, 8).all() and (plen == 2 * f.dlc).all()),
         "all rows consistent", "DLC/payload mismatch or invalid hex")

    # --- time base -----------------------------------------------------------------------------
    ts_ok = (f.timestamp == f.sim_time_ns.map(fmt_time)).all() and (st.timestamp == st.sim_time_ns.map(fmt_time)).all()
    lo, hi = meta["simulation"]["sim_start_ns"], meta["simulation"]["sim_end_ns"] + step_ns
    in_range = f.sim_time_ns.between(lo, hi).all()
    r.ok("V09", "timestamps = SUMO time (sim_time_ns) and inside simulated interval",
         bool(ts_ok and in_range), f"[{fmt_time(lo)}, {fmt_time(hi)}] s", "timestamp/sim_time mismatch or out of range")

    # Each message's first frame must sit exactly at (vehicle's first SUMO state time + configured offset).
    first_state = st.groupby("vehicle_id").sim_time_ns.min()
    align_err = []
    for (v, cid), g in f.groupby(["vehicle_id", "can_id"]):
        prof = profiles[fleet[v]["profile"]]
        db = _dbc(prof["dbc"])
        mname = db.get_message_by_frame_id(int(cid, 16)).name
        off = round(next(m["offset_ms"] for m in prof["messages"] if m["name"] == mname) * 1e6)
        if g.sim_time_ns.min() != first_state[v] + off:
            align_err.append(f"{v}/{cid}")
    r.ok("V10", "CAN frames and SUMO states share one timeline (first frame = first SUMO state + offset)",
         not align_err, "all vehicle/message streams aligned", f"misaligned: {align_err}")

    mono = all(g.sim_time_ns.is_monotonic_increasing for _, g in f.groupby("vehicle_id"))
    cap_mono = all(g.capture_time_ns.is_monotonic_increasing for _, g in f.groupby("vehicle_id"))
    r.ok("V11", "timestamps monotonic per vehicle (SUMO time and capture order)", mono and cap_mono,
         "non-decreasing in both", f"sim monotonic={mono}, capture monotonic={cap_mono}")

    period_viol = 0
    for (v, cid), g in f.groupby(["vehicle_id", "can_id"]):
        prof = profiles[fleet[v]["profile"]]
        mname = _dbc(prof["dbc"]).get_message_by_frame_id(int(cid, 16)).name
        per = round(next(m["period_ms"] for m in prof["messages"] if m["name"] == mname) * 1e6)
        period_viol += int((g.sim_time_ns.diff().dropna() != per).sum())
    r.ok("V12", "every message stream follows its configured period exactly on the SUMO timeline",
         period_viol == 0, "0 gaps or extra frames", f"{period_viol} deviating intervals")

    dup_uid = int(f.frame_uid.duplicated().sum())
    dup_key = int(f.duplicated(["vehicle_id", "sim_time_ns", "can_id"]).sum())
    r.ok("V13", "no duplicate records", dup_uid == 0 and dup_key == 0, "0 duplicates",
         f"dup uid={dup_uid}, dup (vehicle,time,id)={dup_key}")

    # --- transport ---------------------------------------------------------------------------
    t = meta["transport_stats"]["total"]
    clean = t["missing"] == 0 and t["unexpected"] == 0 and t["content_mismatch"] == 0 and t["send_errors"] == 0 and len(unm) == 0
    r.ok("V14", "every transmitted frame captured on vCAN with identical ID+payload; nothing extra",
         clean and t["matched"] == len(f),
         f"sent={t['sent']} captured={t['captured']} matched={t['matched']}", f"{t}, unmatched rows={len(unm)}")

    # --- DBC decode, counters, SUMO->CAN consistency -----------------------------------------------
    dec_err, ctr_err, speed_err = 0, 0, []
    decoded_speed = []
    for (v, cid), g in f.groupby(["vehicle_id", "can_id"]):
        db = _dbc(profiles[fleet[v]["profile"]]["dbc"])
        msg = db.get_message_by_frame_id(int(cid, 16))
        ctr = []
        for _, row in g.iterrows():
            try:
                d = msg.decode(bytes.fromhex(row.payload), decode_choices=False)
            except Exception:
                dec_err += 1
                continue
            ctr.append(int(d.get("Counter", 0)))
            if msg.name == "VEHICLE_SPEED":
                decoded_speed.append((v, row.sim_time_ns, d["VehicleSpeed"]))
        ctr_err += int(sum((b - a) % 16 != 1 for a, b in zip(ctr, ctr[1:])))
    r.ok("V15", "every payload decodes with the vehicle's DBC", dec_err == 0, f"{len(f)} decoded", f"{dec_err} failures")
    r.ok("V16", "rolling counters increment by 1 (mod 16) per message stream", ctr_err == 0, "no counter gaps", f"{ctr_err} gaps")

    if decoded_speed:
        ds = pd.DataFrame(decoded_speed, columns=["vehicle_id", "sim_time_ns", "can_kmh"]).sort_values("sim_time_ns")
        ss = st[["vehicle_id", "sim_time_ns", "speed_mps"]].sort_values("sim_time_ns")
        m = pd.merge_asof(ds, ss, on="sim_time_ns", by="vehicle_id", direction="backward")
        err = (m.can_kmh - m.speed_mps * 3.6).abs()
        tol = 0.01 + 0.0002 * 3.6   # DBC resolution + 4-decimal rounding of speed_mps in vehicle_states.csv
        r.ok("V17", "decoded CAN VehicleSpeed equals SUMO speed (sample-and-hold) for every frame",
             bool((err <= tol).all()), f"max |error| = {err.max():.4f} km/h over {len(m)} frames",
             f"{int((err > tol).sum())} frames exceed {tol:.4f} km/h")

    # --- ground truth -------------------------------------------------------------------------
    r.ok("V18", "every frame has exactly one ground-truth label row (and vice versa)",
         set(lab.frame_uid) == set(f.frame_uid) and not lab.frame_uid.duplicated().any(),
         f"{len(lab)} label rows", "label/frame sets differ")
    n_mal = int((lab.frame_label != "benign").sum())
    if n_mal == 0 and not meta["attack_configuration"]:
        r.add("V19", "malicious frames map to attack ground truth", "NA", "no attacks configured (scenario S0); 0 malicious frames")
    else:
        r.add("V19", "malicious frames map to attack ground truth", "FAIL", "attack ground truth not implemented yet")
    cids = set(lab.campaign_id) - {"None"}
    if not cids and camp.empty and not meta["campaign_configuration"]:
        for vid, name in [("V20", "campaign IDs exist in campaign ground truth"),
                          ("V21", "coordinated campaigns contain configured vehicles; relative delays match"),
                          ("V22", "independent attacks not assigned the same campaign")]:
            r.add(vid, name, "NA", "no campaigns configured (scenario S0)")
    else:
        r.add("V20", "campaign checks", "FAIL", "campaign validation not implemented yet")

    # --- reproducibility -----------------------------------------------------------------------
    absent = [k for k in REQUIRED_METADATA if _get(meta, k) in (None, "", {}, [])]
    r.ok("V23", "required metadata present (seed, versions, time base, config hashes)", not absent,
         f"seed={meta.get('random_seed')}, SUMO {meta['simulation']['sumo_version']}", f"missing: {absent}")
    snap_bad = [n for n, h in meta["config_sha256"].items()
                if not (p["snapshot"] / n).exists() or sha256(p["snapshot"] / n) != h]
    r.ok("V24", "configuration snapshot saved and hashes match", not snap_bad,
         f"{len(meta['config_sha256'])} files", f"bad: {snap_bad}")
    if not _get(meta, "git.commit"):
        r.add("V25", "code version recorded (git commit)", "WARN", "no git commit recorded")
    elif _get(meta, "git.dirty"):
        r.add("V25", "code version recorded (git commit)", "WARN", f"{meta['git']['commit'][:10]} with uncommitted changes")
    else:
        r.add("V25", "code version recorded (git commit)", "PASS", meta["git"]["commit"][:10])

    # --- informational timing statistics ----------------------------------------------------------
    r.info["frames_per_vehicle"] = f.groupby("vehicle_id").size().to_dict()
    r.info["frames_per_vehicle_and_id"] = {f"{v}/{c}": int(n) for (v, c), n in f.groupby(["vehicle_id", "can_id"]).size().items()}
    dur = (f.sim_time_ns.max() - f.sim_time_ns.min()) / 1e9
    r.info["sim_span_s"] = round(dur, 6)
    r.info["observed_rate_fps_per_vehicle"] = {v: round(n / dur, 2) for v, n in r.info["frames_per_vehicle"].items()}
    if len(f) and (f.capture_offset_us != "").all():
        off = f.capture_offset_us.astype(float)
        r.info["capture_offset_us"] = {v: _q(g.astype(float)) for v, g in off.groupby(f.vehicle_id)}
        jit = []
        for _, g in f.groupby(["vehicle_id", "can_id"]):
            jit.append((g.capture_time_ns.diff() - g.sim_time_ns.diff()).dropna() / 1e3)
        r.info["inter_arrival_error_us"] = _q(pd.concat(jit))
        if r.info["capture_offset_us"] and max(v["p99"] for v in r.info["capture_offset_us"].values()) > 5000:
            r.add("V26", "capture jitter (p99 capture offset)", "WARN", "> 5 ms; host load or VM scheduling")
        else:
            r.add("V26", "capture jitter (p99 capture offset)", "PASS", "<= 5 ms for all vehicles")
    return r


def _q(s: pd.Series) -> dict:
    s = s.astype(float)
    return {"n": int(len(s)), "mean": round(float(s.mean()), 1), "std": round(float(s.std()), 1),
            "p50": round(float(np.percentile(s, 50)), 1), "p95": round(float(np.percentile(s, 95)), 1),
            "p99": round(float(np.percentile(s, 99)), 1), "min": round(float(s.min()), 1), "max": round(float(s.max()), 1)}


_DBC_CACHE: dict[str, cantools.database.can.Database] = {}


def _dbc(rel: str):
    if rel not in _DBC_CACHE:
        _DBC_CACHE[rel] = cantools.database.load_file(str(PROJECT_ROOT / rel))
    return _DBC_CACHE[rel]


def write_report(r: Report, sid: str, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j = out_dir / "validation_report.json"
    j.write_text(json.dumps({"session_id": sid, "overall": r.overall, "checks": r.checks, "info": r.info}, indent=2, default=str))
    lines = [f"# Validation report: {sid}", "", f"**Overall: {r.overall}**", "",
             "| ID | Check | Status | Detail |", "|---|---|---|---|"]
    lines += [f"| {c['id']} | {c['check']} | {c['status']} | {c['detail']} |" for c in r.checks]
    lines += ["", "## Timing / traffic statistics (informational)", "", "```json", json.dumps(r.info, indent=2, default=str), "```"]
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
