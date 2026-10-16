"""Milestone 6: execute the frozen fleetcan-sim-1.1 matrix (config/final/matrix/matrix_spec.json).

  --preflight  load every run's configuration through the real config / campaign / scenario / attack code
               (no SUMO, no vCAN) and stop on the first inconsistency
  (default)    for every run in spec order: generate (fast mode, Parquet, the run's own seed and session id),
               then the frozen descriptor analysis; one JSON line per run in
               data/final/fleetcan-sim-1.1/run_log.jsonl

Rules: an existing session is never overwritten (it is logged as 'existing' and skipped); a failed run is
recorded with its original seed and is never re-run with another seed.

Usage: .venv/bin/python scripts/m6_run_matrix.py [--preflight] [--limit K]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SPEC = ROOT / "config" / "final" / "matrix" / "matrix_spec.json"
FINAL = ROOT / "data" / "final" / "fleetcan-sim-1.1"


def preflight(runs) -> None:
    from src.attack_engine import build_attacks, resolve_semantic_targets
    from src.campaign_scheduler import attack_configs, build_schedule, check_scenario
    from src.config import load_experiment
    for r in runs:
        cfg = load_experiment(r["experiment_config"])
        assert cfg.seed == r["seed"] and cfg.dataset_version == "fleetcan-sim-1.1" and not cfg.realtime
        assert cfg.output_format == "parquet" and len(cfg.fleet) == r["fleet_size"]
        assert cfg.fleet_info["ecu_jitter"]["fraction"] == 0.01 and cfg.fleet_info["switch_on"]["range_s"] == [0.0, 1.0]
        assert cfg.fleet_info["profile_assignment"]["strategy"] == "balanced_shuffle"
        for p in cfg.profiles.values():
            b = p.bus
            assert (b.bitrate, b.stuffing, b.queue_limit, b.drop_policy, b.node_queue_order, b.expiry_policy,
                    b.max_frame_age_ms) == (500000, "exact", 32, "drop_newest", "priority", "deadline", 100.0)
        camps = build_schedule(cfg.campaigns, cfg.seed, [a.vehicle_id for a in cfg.fleet])
        problems = check_scenario(cfg.scenario_id, cfg.attacks, camps)
        assert not problems, (r["session_id"], problems)
        by_v = {a.vehicle_id: cfg.profiles[a.profile] for a in cfg.fleet}
        build_attacks(resolve_semantic_targets(list(cfg.attacks) + attack_configs(camps), by_v), cfg.fleet, by_v, cfg.seed,
                      campaign_ids={c.campaign_id for c in camps})
    print(f"preflight OK: {len(runs)} runs load, schedule and pass scenario checks with the frozen settings")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    runs = json.loads(SPEC.read_text())["runs"]
    if a.preflight:
        preflight(runs)
        return
    FINAL.mkdir(parents=True, exist_ok=True)
    log = FINAL / "run_log.jsonl"
    py = str(ROOT / ".venv" / "bin" / "python")
    done = 0
    for r in runs:
        if a.limit is not None and done >= a.limit:
            break
        sid = r["session_id"]
        rec = {"run_index": r["run_index"], "session_id": sid, "config_id": r["config_id"], "repetition": r["repetition"],
               "seed": r["seed"]}
        if (ROOT / "data" / "metadata" / sid / "metadata.json").exists():
            rec["status"] = "existing"
        else:
            t0 = time.time()
            g = subprocess.run([py, "-m", "src.orchestrator", "--config", r["experiment_config"], "--session-id", sid],
                               cwd=ROOT, capture_output=True, text=True)
            m = re.findall(r"VALIDATION: ([A-Z]+)", g.stdout)
            rec.update({"generator_exit": g.returncode, "generator_validation": m[-1] if m else "NO_RESULT",
                        "generation_wall_s": round(time.time() - t0, 2)})
            if g.returncode != 0 or not m:
                rec["generator_tail"] = (g.stdout + g.stderr)[-2000:]
            t1 = time.time()
            an = subprocess.run([py, "-m", "analysis.run_session", sid], cwd=ROOT, capture_output=True, text=True)
            rec.update({"analysis_exit": an.returncode, "analysis_wall_s": round(time.time() - t1, 2),
                        "analysis_line": an.stdout.strip()[-300:]})
            rec["status"] = "done"
            done += 1
        with open(log, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(json.dumps({k: rec.get(k) for k in ("run_index", "session_id", "status", "generator_validation", "analysis_exit")}), flush=True)


if __name__ == "__main__":
    main()
