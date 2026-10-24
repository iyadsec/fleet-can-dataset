"""M12: generate fleetcan-sim-2.0 sessions from configs/fleetcan_sim_2_0.yaml (sequential, resumable).

For each selected session: write experiment / campaigns / attacks YAML under OUT/configs/<sid>/, run the
orchestrator (fast mode, Parquet, validation on), and record the outcome in OUT/session_status.csv. A session
counts as complete only if its metadata exists and its validation report says PASS. Sessions run one at a time
(the vCAN interfaces are shared). Free disk is checked before every session.

Usage:
  .venv/bin/python scripts/run_fcs2_generation.py --out ~/fcs2_data --sessions SID ...
  .venv/bin/python scripts/run_fcs2_generation.py --out ~/fcs2_data --pilot          # stratified pilot (~30)
  .venv/bin/python scripts/run_fcs2_generation.py --out ~/fcs2_data --all [--expect-sha SHA]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "configs" / "fleetcan_sim_2_0.yaml"
PY = str(ROOT / ".venv" / "bin" / "python")
MIN_FREE_GB = 6.0
STATUS_COLS = ["session_id", "status", "validation", "frames", "wall_s", "utc"]


def free_gb(p: Path) -> float:
    return shutil.disk_usage(p).free / 1e9


def scenario_id(s: dict) -> str:
    return {"S0": "S0", "S1": "S1", "S3": "S3", "S3-mixed": "S3", "S3-probe": "S3"}.get(s["scenario"], "S2")


def write_configs(s: dict, out: Path, matrix_sha: str) -> Path:
    d = out / "configs" / s["session_id"]
    d.mkdir(parents=True, exist_ok=True)
    camps = []
    for c in s["campaigns"]:
        c = {k: v for k, v in c.items() if k != "pattern_info"}
        camps.append(c)
    (d / "campaigns.yaml").write_text(yaml.safe_dump({"campaigns": camps}, sort_keys=False))
    (d / "attacks.yaml").write_text(yaml.safe_dump({"attacks": s["attacks"]}, sort_keys=False))
    tags = {k: s[k] for k in ("config_id", "repetition", "seed", "scenario", "N", "family", "K", "pattern", "strength",
                              "hard_negative", "independent_count", "command_s")}
    tags.update({"matrix_sha256": matrix_sha, "fleet_composition": s["fleet_composition"],
                 "pattern_info": {c["campaign_id"]: c.get("pattern_info", {}) for c in s["campaigns"]}})
    exp = {"experiment": {"experiment_id": s["session_id"], "dataset_version": "fleetcan-sim-2.0",
                          "scenario_id": scenario_id(s), "description": f"fleetcan-sim-2.0 {s['config_id']} R{s['repetition']}",
                          "seed": s["seed"], "tags": tags},
           "simulation": {"route_generation": {"method": "grid_loops_v1", "network": "sumo/network/grid4x4.net.xml"},
                          "step_length_s": 0.01, "duration_s": s["duration_s"], "realtime": False},
           "can": {"transport": "socketcan", "bus_model": "logical", "frame_format": "classical", "capture_drain_s": 0.5},
           "output": {"root": str(out)}, "storage": {"format": "parquet"},
           "fleet_config": f"config/fcs2/fleets/fleet_{s['N']}.yaml",
           "attacks_config": str(d / "attacks.yaml"), "campaigns_config": str(d / "campaigns.yaml")}
    (d / "experiment.yaml").write_text(yaml.safe_dump(exp, sort_keys=False))
    return d / "experiment.yaml"


def read_status(out: Path) -> dict:
    f = out / "session_status.csv"
    if not f.exists():
        return {}
    with open(f) as fh:
        return {r["session_id"]: r for r in csv.DictReader(fh)}


def append_status(out: Path, row: dict):
    f = out / "session_status.csv"
    new = not f.exists()
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, STATUS_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def pilot(sessions: list[dict]) -> list[dict]:
    """R1 of a stratified set: every scenario, every S3 pattern, every strength, every hard-negative type."""
    want, out = set(), []
    for s in sessions:
        if s["repetition"] != 1:
            continue
        key = (s["scenario"], s["pattern"], s["strength"] if s["scenario"] == "S3" else s["hard_negative"],
               s["family"] if s["scenario"] in ("S1", "S2-diverse") else "")
        if key not in want and (s["N"] <= 50 or s["scenario"] == "S0"):
            want.add(key)
            out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--sessions", nargs="*")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--expect-sha")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    matrix_sha = hashlib.sha256(MATRIX.read_bytes()).hexdigest()
    if a.expect_sha and matrix_sha != a.expect_sha:
        sys.exit(f"STOP: matrix SHA {matrix_sha} != expected {a.expect_sha}")
    sessions = yaml.safe_load(MATRIX.read_text())["sessions"]
    if a.sessions:
        sel = [s for s in sessions if s["session_id"] in set(a.sessions)]
    elif a.pilot:
        sel = pilot(sessions)
    elif a.all:
        sel = sessions
    else:
        sys.exit("choose --sessions, --pilot or --all")
    done = {k for k, r in read_status(out).items() if r["status"] == "ok"}
    todo = [s for s in sel if s["session_id"] not in done]
    print(f"{len(sel)} selected, {len(todo)} to run, matrix {matrix_sha[:12]}", flush=True)
    t_all = time.time()
    for i, s in enumerate(todo, 1):
        sid = s["session_id"]
        if free_gb(out) < MIN_FREE_GB:
            sys.exit(f"STOP: less than {MIN_FREE_GB} GB free")
        for sub in ("raw", "ground_truth", "validation", "metadata", "generated_sumo"):
            p = out / sub / sid
            if p.exists():
                shutil.rmtree(p)                       # incomplete earlier attempt (not marked ok)
        cfg = write_configs(s, out, matrix_sha)
        logf = out / "logs" / f"{sid}.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        with open(logf, "w") as lf:
            rc = subprocess.run([PY, "-m", "src.orchestrator", "--config", str(cfg), "--session-id", sid], cwd=ROOT,
                                stdout=lf, stderr=subprocess.STDOUT).returncode
        vr = out / "validation" / sid / "validation_report.json"
        verdict = json.loads(vr.read_text())["overall"] if vr.exists() else "MISSING"
        frames = ""
        md = out / "metadata" / sid / "metadata.json"
        if md.exists():
            frames = json.loads(md.read_text())["transport_stats"]["total"]["captured"]
        status = "ok" if rc == 0 and verdict == "PASS" and md.exists() else "failed"
        append_status(out, {"session_id": sid, "status": status, "validation": verdict, "frames": frames,
                            "wall_s": round(time.time() - t0, 1), "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print(f"[{i}/{len(todo)}] {sid} {status} {verdict} frames={frames} {time.time() - t0:.0f}s "
              f"total={(time.time() - t_all) / 3600:.2f}h free={free_gb(out):.1f}GB", flush=True)


if __name__ == "__main__":
    main()
