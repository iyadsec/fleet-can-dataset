"""Milestone 9.1: ENGINEERING-ONLY resumability test (not a research session, not part of any dataset).

One short N = 10 session (frozen baseline fleet, generated grid_loops_v1 routes, coordinated fuzzy K = 5, 0.5 s
stagger, 22 s — the G0 configuration shape) under the engineering ID M91_RESUME_N010, seed 83110:

  1  absent before start
  2  generator killed (SIGKILL, whole process group) mid-run -> staged_incomplete, no metadata, nothing published
  3  re-run of the same ID completes (stage cleared automatically)       -> analysis_pending, validation PASS
  4  analysis                                                           -> complete; provenance tags present
  5  third run of the same ID is refused; every published byte unchanged
  6  analysis interrupted (descriptor_validation.json missing)          -> analysis_pending; re-run reproduces descriptors
  7  crash during publication simulated (metadata dir moved away)       -> partial_published; clear_partial; regeneration
     reproduces identical raw / ground-truth / descriptor tables
Result: results/m9_1_freeze/resumability_test.json

Usage: .venv/bin/python scripts/m91_resume_test.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from session_state import clear_partial, session_state  # noqa: E402

SID = "M91_RESUME_N010"
SEED = 83110
CFG = ROOT / "config" / "m91" / "experiments"
DATA = ROOT / "data"
PY = str(ROOT / ".venv" / "bin" / "python")
OUT = ROOT / "results" / "m9_1_freeze" / "resumability_test.json"


def matrix_identity():
    man = ROOT / "results" / "m9_1_freeze" / "matrix_freeze_manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        return m["matrix_id"], m["matrix_sha256"]
    import m91_freeze as F                       # identity only: the test never reads sessions from the matrix
    return F.MATRIX_ID, hashlib.sha256(F.MATRIX.read_bytes()).hexdigest()


def write_config(mid, msha):
    CFG.mkdir(parents=True, exist_ok=True)
    camp = CFG / f"{SID}.campaigns.yaml"
    camp.write_text("# M9.1 engineering resumability test (not research data)\ncampaigns:\n"
                    "  - campaign_id: C_S3_FUZZY\n    attack_type: fuzzy\n    coordinated: true\n"
                    "    targets: [vehicle_01, vehicle_02, vehicle_03, vehicle_04, vehicle_05]\n    start_s: 10.0\n"
                    "    delay_pattern: fixed\n    relative_delays_s: [0.0, 0.5, 1.0, 1.5, 2.0]\n    duration_s: 5.0\n"
                    "    params: {mode: both, rate_hz: 1000, id_range: [0x000, 0x7FF], dlc: random}\n")
    exp = CFG / f"{SID}.yaml"
    exp.write_text(f"""# M9.1 engineering resumability test (engineering only; not part of any research dataset).
experiment:
  experiment_id: {SID}
  dataset_version: engineering-m91-resume-test
  scenario_id: S3
  description: "M9.1 resumability test, G0-shaped (N=10 baseline fleet, generated routes)"
  seed: {SEED}
  tags: {{component: engineering-m91-resume-test, matrix_id: {mid}, matrix_sha256: {msha}, config_id: ENGINEERING, session_id: {SID},
         repetition: 1, N: 10, K: 5, scenario: S3, attack: fuzzy, temporal_pattern: small, route_source: "generated:grid_loops_v1",
         coordinated_members: [vehicle_01, vehicle_02, vehicle_03, vehicle_04, vehicle_05]}}
simulation:
  route_generation: {{method: grid_loops_v1, network: sumo/network/grid4x4.net.xml}}
  step_length_s: 0.01
  duration_s: 22.0
  realtime: false
can:
  transport: socketcan
  bus_model: logical
  frame_format: classical
  capture_drain_s: 0.5
output:
  root: data
storage:
  format: parquet
fleet_config: config/final/fleets/fleet_10.yaml
attacks_config: config/attacks.yaml
campaigns_config: {camp.relative_to(ROOT)}
""")
    return exp


def gen(exp):
    return subprocess.run([PY, "-m", "src.orchestrator", "--config", str(exp), "--session-id", SID], cwd=ROOT, capture_output=True, text=True)


def analyse():
    return subprocess.run([PY, "-m", "analysis.run_session", SID], cwd=ROOT, capture_output=True, text=True)


def file_hashes():
    out = {}
    for d in ("raw", "ground_truth", "validation", "metadata", "analysis"):
        for f in sorted((DATA / d / SID).rglob("*")):
            if f.is_file():
                out[str(f.relative_to(DATA))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def table_hashes():
    import pyarrow.parquet as pq
    out = {}
    for d in ("raw", "ground_truth", "analysis"):
        for f in sorted((DATA / d / SID).glob("*.parquet")):
            out[str(f.relative_to(DATA))] = hashlib.sha256(pq.read_table(f).to_pandas().to_csv(index=False).encode()).hexdigest()
    return out


def main():
    mid, msha = matrix_identity()
    exp = write_config(mid, msha)
    res = {"session_id": SID, "seed": SEED, "engineering_only": True, "steps": {}}
    st = res["steps"]
    s0 = session_state(SID)["state"]
    if s0 != "absent":
        sys.exit(f"{SID} already exists ({s0}); remove it deliberately before re-running this test")
    st["1_before"] = s0
    # 2 kill mid-run
    p = subprocess.Popen([PY, "-m", "src.orchestrator", "--config", str(exp), "--session-id", SID], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time()
    while not (DATA / ".incomplete" / SID).exists() and time.time() - t0 < 60:
        time.sleep(0.1)
    time.sleep(3.0)
    alive = p.poll() is None
    os.killpg(p.pid, signal.SIGKILL)
    p.wait()
    time.sleep(1.0)
    s2 = session_state(SID)
    leftover = subprocess.run(["pgrep", "-f", f"[s]umo.*{SID}"], capture_output=True, text=True).stdout.split()
    st["2_after_kill"] = {"killed_while_running": alive, "state": s2["state"], "published": s2["present"], "stage_exists": s2["staged"],
                          "orphan_processes": leftover}
    # 3 resume
    g = gen(exp)
    s3 = session_state(SID)
    st["3_resume"] = {"exit": g.returncode, "validation": s3.get("generator_validation"), "state": s3["state"],
                      "stage_left": (DATA / ".incomplete" / SID).exists()}
    # 4 analysis
    an = analyse()
    s4 = session_state(SID, expect={"matrix_id": mid, "matrix_sha256": msha})
    meta = json.loads((DATA / "metadata" / SID / "metadata.json").read_text())
    st["4_analysis"] = {"exit": an.returncode, "state": s4["state"], "tags": meta["tags"],
                        "versions": meta["versions"], "git_commit_recorded": bool(meta.get("git")), "seed": meta["random_seed"]}
    first_files, first_tables = file_hashes(), table_hashes()
    # 5 refuse overwrite
    g5 = gen(exp)
    st["5_rerun_refused"] = {"exit": g5.returncode, "refused": "refusing to overwrite" in (g5.stdout + g5.stderr),
                             "all_bytes_unchanged": file_hashes() == first_files, "state": session_state(SID)["state"]}
    # 6 interrupted analysis
    (DATA / "analysis" / SID / "descriptor_validation.json").unlink()
    s6a = session_state(SID)["state"]
    an6 = analyse()
    st["6_analysis_resume"] = {"state_after_interrupt": s6a, "exit": an6.returncode, "state": session_state(SID)["state"],
                               "descriptor_tables_identical": table_hashes() == first_tables}
    # keep a copy of the first complete run for the logical comparison after regeneration
    first = SID + "_FIRST"
    for d in ("raw", "ground_truth", "validation", "metadata", "analysis"):
        shutil.copytree(DATA / d / SID, DATA / d / first)
    # 7 crash during publication
    hold = DATA / ".m91_test_hold"
    hold.mkdir(exist_ok=True)
    shutil.move(str(DATA / "metadata" / SID), str(hold / SID))
    s7a = session_state(SID)["state"]
    refused_partial = gen(exp)
    removed = clear_partial(SID)
    s7b = session_state(SID)["state"]
    g7 = gen(exp)
    an7 = analyse()
    st["7_partial_publication"] = {"state_after_crash": s7a, "orchestrator_refuses_partial": refused_partial.returncode != 0,
                                   "cleared": removed, "state_after_clear": s7b, "regen_exit": g7.returncode, "analysis_exit": an7.returncode,
                                   "state": session_state(SID, expect={"matrix_sha256": msha})["state"],
                                   "file_level_table_hashes_identical": table_hashes() == first_tables}
    from compare_sessions import compare
    cmp = compare(DATA, first, SID)
    st["7_partial_publication"]["logical_comparison_vs_first_run"] = cmp
    st["7_partial_publication"]["tables_identical_to_first_run"] = all(str(v).startswith("IDENTICAL") for v in cmp.values())
    for d in ("raw", "ground_truth", "validation", "metadata", "analysis"):
        shutil.rmtree(DATA / d / first)
    shutil.rmtree(hold)
    ok = (st["2_after_kill"]["killed_while_running"] and st["2_after_kill"]["state"] == "staged_incomplete"
          and not any(st["2_after_kill"]["published"].values()) and st["3_resume"]["exit"] == 0 and st["3_resume"]["state"] == "analysis_pending"
          and st["3_resume"]["validation"] == "PASS" and st["4_analysis"]["state"] == "complete"
          and st["5_rerun_refused"]["refused"] and st["5_rerun_refused"]["all_bytes_unchanged"] and st["5_rerun_refused"]["exit"] != 0
          and s6a == "analysis_pending" and st["6_analysis_resume"]["state"] == "complete" and st["6_analysis_resume"]["descriptor_tables_identical"]
          and s7a == "partial_published" and st["7_partial_publication"]["orchestrator_refuses_partial"] and s7b == "absent"
          and st["7_partial_publication"]["state"] == "complete" and st["7_partial_publication"]["tables_identical_to_first_run"])
    res["verdict"] = "PASS" if ok else "FAIL"
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
