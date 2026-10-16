"""Milestone 8.1: memory-scalability runs and equivalence checks (generator 1.3.0). Engineering tests only.

  run NAME... [--chunk-rows K] [--suffix S]   run config/m81/experiments/NAME.yaml (or config/m8/experiments/NAME.yaml)
                                              with resource sampling of the orchestrator AND the analysis process
  regress SID...                              re-run frozen fleetcan-sim-1.1 configurations as M81_REG_<sid>
  compare A B                                 tables + descriptors + labels + bus_stats + metadata + validation report
  configs                                     write config/m81/ (longer N=100 tests)

Rows -> results/m8_1_memory_scalability/memory_metrics.csv; comparisons -> results/m8_1_memory_scalability/compare/.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from compare_sessions import compare  # noqa: E402
from m8_scaling import EXPERIMENT, FLEET, TICK, _mb, _sample, camp, vid  # noqa: E402
from src import storage  # noqa: E402

DATA = ROOT / "data"
CFG = ROOT / "config" / "m81"
OUT = ROOT / "results" / "m8_1_memory_scalability"
PY = str(ROOT / ".venv" / "bin" / "python")
FIELDS = ["session_id", "fleet_size", "duration_s", "seed", "scenario", "attack_type", "campaign_size", "chunk_rows",
          "wall_time_s", "real_time_factor", "cpu_mean_percent", "cpu_peak_percent", "memory_start_mb", "memory_peak_mb",
          "memory_end_mb", "analysis_peak_mb", "analysis_wall_s", "swap_used_delta_mb", "fd_peak", "thread_peak",
          "frames_generated", "frames_transmitted", "frames_captured", "queue_drops", "expired_drops", "capture_loss",
          "flow_control_timeouts", "descriptor_count", "temp_storage_peak_mb", "output_size_mb", "validation_status"]


def swap_used_mb() -> float:
    m = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
    return (m["SwapTotal"] - m["SwapFree"]) / 1024


def _sampled(cmd: list[str], stage: Path | None = None) -> tuple[subprocess.CompletedProcess, list[dict], float]:
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    samples, tmp_peak, t0 = [], 0.0, time.perf_counter()
    while p.poll() is None:
        s = _sample(p.pid)
        if stage is not None and stage.exists():
            tmp_peak = max(tmp_peak, _mb(stage))
        s["tmp_mb"] = tmp_peak
        samples.append(s)
        time.sleep(0.25)
    out = p.stdout.read()
    return subprocess.CompletedProcess(cmd, p.returncode, out), [s for s in samples if s["procs"]], time.perf_counter() - t0


def run_one(cfg_path: Path, sid: str, chunk_rows: int | None = None) -> dict:
    swap0 = swap_used_mb()
    cmd = [PY, "-m", "src.orchestrator", "--config", str(cfg_path), "--session-id", sid]
    if chunk_rows:
        cmd += ["--chunk-rows", str(chunk_rows)]
    g, smp, _ = _sampled(cmd, DATA / ".incomplete" / sid)
    (OUT / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "logs" / f"{sid}.log").write_text(g.stdout)
    if g.returncode != 0:
        raise RuntimeError(f"{sid}: orchestrator exit {g.returncode}\n{g.stdout[-3000:]}")
    a, asmp, a_wall = _sampled([PY, "-m", "analysis.run_session", sid])
    (OUT / "logs" / f"{sid}.analysis.log").write_text(a.stdout)
    swap1 = max(swap_used_mb(), swap0)
    md = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
    val = json.loads((DATA / "validation" / sid / "validation_report.json").read_text())
    dval = json.loads((DATA / "analysis" / sid / "descriptor_validation.json").read_text())
    cpu = [100.0 * (b["cpu_ticks"] - x["cpu_ticks"]) / TICK / (b["t"] - x["t"]) for x, b in zip(smp, smp[1:]) if b["t"] > x["t"]]
    bt, tot = md["can"]["bus_totals"], md["transport_stats"]["total"]
    sim_s = md["simulation"]["sim_end_ns"] / 1e9
    wall = md["simulation"]["execution"]["wall_seconds"]
    tags = md.get("tags", {})
    ndesc = len(pd.read_parquet(DATA / "analysis" / sid / "descriptors.parquet", columns=["descriptor_id"]))
    row = {"session_id": sid, "fleet_size": md["fleet"]["vehicle_count"], "duration_s": sim_s, "seed": md["random_seed"],
           "scenario": md["scenario_id"], "attack_type": tags.get("attack"), "campaign_size": tags.get("campaign_size"),
           "chunk_rows": md["simulation"]["execution"].get("recorder", {}).get("chunk_rows"),
           "wall_time_s": wall, "real_time_factor": round(sim_s / wall, 4),
           "cpu_mean_percent": round(float(np.mean(cpu)), 1) if cpu else None, "cpu_peak_percent": round(float(np.max(cpu)), 1) if cpu else None,
           "memory_start_mb": round(smp[0]["rss_kb"] / 1024, 1), "memory_peak_mb": round(max(s["rss_kb"] for s in smp) / 1024, 1),
           "memory_end_mb": round(smp[-1]["rss_kb"] / 1024, 1),
           "analysis_peak_mb": round(max(s["rss_kb"] for s in asmp) / 1024, 1) if asmp else None, "analysis_wall_s": round(a_wall, 1),
           "swap_used_delta_mb": round(swap1 - swap0, 1), "fd_peak": max(s["fds"] for s in smp), "thread_peak": max(s["threads"] for s in smp),
           "frames_generated": sum(v["generated"] for v in bt.values()), "frames_transmitted": sum(v["transmitted"] for v in bt.values()),
           "frames_captured": tot.get("captured"), "queue_drops": sum(v["queue_dropped"] for v in bt.values()),
           "expired_drops": sum(v["expired_dropped"] for v in bt.values()),
           "capture_loss": sum(v["transmitted"] for v in bt.values()) - int(tot.get("captured", 0)),
           "flow_control_timeouts": md["simulation"]["execution"]["flow_control_timeouts"], "descriptor_count": ndesc,
           "temp_storage_peak_mb": round(max(s["tmp_mb"] for s in smp), 1),
           "output_size_mb": round(sum(_mb(DATA / d / sid) for d in ("raw", "ground_truth", "analysis", "metadata", "validation")), 2),
           "validation_status": f"generator={val['overall']};descriptor={dval['overall']};analysis_exit={a.returncode}"}
    f = OUT / "memory_metrics.csv"
    new = not f.exists()
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    print(json.dumps(row), flush=True)
    return row


# ------------------------------------------------------------------ equivalence
VOLATILE_META = {"session_id", "versions", "git", "wall_clock", "config_sha256", "software", "description", "experiment_id"}


def _flat(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else k))
    else:
        out[prefix] = d
    return out


def compare_full(a: str, b: str) -> dict:
    tables = compare(DATA, a, b)
    labels = {}
    for t in ("descriptor_labels",):
        ta, tb = (storage.read_table(DATA / "analysis" / s / t) for s in (a, b))
        cols = [c for c in ta.columns if c not in ("descriptor_id", "session_id")]
        labels[t] = "IDENTICAL" if ta[cols].reset_index(drop=True).equals(tb[cols].reset_index(drop=True)) else "DIFFERENT"
    ba, bb = (json.loads((DATA / "metadata" / s / "bus_stats.json").read_text()) for s in (a, b))
    ma, mb = (json.loads((DATA / "metadata" / s / "metadata.json").read_text()) for s in (a, b))
    fa, fb = _flat({k: v for k, v in ma.items() if k not in VOLATILE_META}), _flat({k: v for k, v in mb.items() if k not in VOLATILE_META})
    meta_diff = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
    expected_meta = [k for k in meta_diff if k.startswith(("simulation.execution.", "time_base.wall_anchor", "simulation.sumo_command",
                                                           "dataset_version"))]
    va, vb = (json.loads((DATA / "validation" / s / "validation_report.json").read_text()) for s in (a, b))
    skip = {"V25", "V27"}                                  # git commit / generator version (expected to differ)
    import re

    def logical(c, sid):                                   # capture-layer (host wall clock) numbers are not research timing
        d = c["detail"].replace(sid, "<sid>")
        if c["id"] == "C05":
            d = re.sub(r"deviate up to [0-9.]+ ms", "deviate up to <capture> ms", d)
        return (c["id"], c["status"], d)

    def info_logical(info):
        info = json.loads(json.dumps(info))
        for rows in (info.get("campaign_delay_by_layer") or {}).values():
            for r in rows:
                r.pop("first_capture_delay_s", None)
        info.pop("capture_offset_us", None)
        return info
    ca = [logical(c, a) for c in va["checks"] if c["id"] not in skip]
    cb = [logical(c, b) for c in vb["checks"] if c["id"] not in skip]
    da, db = (json.loads((DATA / "analysis" / s / "descriptor_validation.json").read_text()) for s in (a, b))
    out = {"a": a, "b": b, "tables": tables, "labels": labels, "bus_stats_identical": ba == bb,
           "metadata_differences": meta_diff, "metadata_unexpected_differences": [k for k in meta_diff if k not in expected_meta],
           "validation_checks_identical": ca == cb, "validation_info_identical": info_logical(va["info"]) == info_logical(vb["info"]),
           "capture_layer_differences": [c["id"] for c, d in zip(va["checks"], vb["checks"]) if c["id"] == "C05" and c["detail"] != d["detail"]]
                                        + (["info.campaign_delay_by_layer.first_capture_delay_s"] if va["info"].get("campaign_delay_by_layer") != vb["info"].get("campaign_delay_by_layer") else []),
           "validation_overall": [va["overall"], vb["overall"]],
           "descriptor_validation_identical": [(c["id"], c["status"], c["detail"]) for c in da["checks"]] == [(c["id"], c["status"], c["detail"]) for c in db["checks"]]}
    out["identical"] = (all(v.startswith("IDENTICAL") for v in tables.values()) and all(v == "IDENTICAL" for v in labels.values())
                        and out["bus_stats_identical"] and not out["metadata_unexpected_differences"] and out["validation_checks_identical"]
                        and out["validation_info_identical"] and out["descriptor_validation_identical"])
    (OUT / "compare").mkdir(parents=True, exist_ok=True)
    (OUT / "compare" / f"{a}__vs__{b}.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("a", "b", "identical", "bus_stats_identical", "metadata_unexpected_differences",
                                          "validation_checks_identical", "validation_info_identical", "descriptor_validation_identical")}))
    if not out["identical"]:
        print(json.dumps(out, indent=1, default=str))
    return out


def write_configs() -> None:
    """Long N=100 engineering tests (not research configurations). Same frozen fleet/CAN/attack settings as M8;
    the 40 s run reuses the M8 seed so its output can be compared with the generator 1.2.0 session M8_S0_N100."""
    d = CFG / "experiments"
    d.mkdir(parents=True, exist_ok=True)
    exps = [dict(eid=f"M81_S0_N100_D{dur:03d}", seed=80100, scen="S0", dur=float(dur), attack="none", size=0, pattern="none")
            for dur in (80, 120, 180)]
    exps.append(dict(eid="M81_CAMP_N100_fuzzy_s10_large", seed=81101, scen="S3", dur=10 + 90 + 5 + 5, attack="fuzzy", size=10,
                     pattern="large", body=camp("C_M81_FUZZ", "fuzzy", [vid(i) for i in range(1, 11)], 10.0, [10.0 * i for i in range(10)])))
    for e in exps:
        campaigns = "config/campaigns.yaml"
        if e.get("body"):
            (d / f"{e['eid']}.campaigns.yaml").write_text("campaigns:\n" + e["body"])
            campaigns = f"config/m81/experiments/{e['eid']}.campaigns.yaml"
        txt = EXPERIMENT.format(eid=e["eid"], dv="m81-memory-test", scen=e["scen"], desc=e["eid"], seed=e["seed"], n=100,
                                attack=e["attack"], size=e["size"], pattern=e["pattern"], dur=e["dur"],
                                attacks="config/attacks.yaml", campaigns=campaigns)
        (d / f"{e['eid']}.yaml").write_text(txt.replace("# Milestone 8 scalability test", "# Milestone 8.1 memory test"))
    print([e["eid"] for e in exps])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "regress", "compare", "configs"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--chunk-rows", type=int, default=None)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "run":
        for name in a.args:
            p = CFG / "experiments" / f"{name}.yaml"
            run_one(p if p.exists() else ROOT / "config" / "m8" / "experiments" / f"{name}.yaml", name + a.suffix, a.chunk_rows)
    elif a.cmd == "regress":
        spec = {r["session_id"]: r for r in json.loads((ROOT / "config/final/matrix/matrix_spec.json").read_text())["runs"]}
        (CFG / "regression").mkdir(parents=True, exist_ok=True)
        for sid in a.args:
            src = (ROOT / spec[sid]["experiment_config"]).read_text()
            assert "dataset_version: fleetcan-sim-1.1\n" in src
            p = CFG / "regression" / f"{sid}.yaml"
            p.write_text(src.replace("dataset_version: fleetcan-sim-1.1\n", "dataset_version: m81-regression-test\n"))
            run_one(p, "M81_REG_" + sid + a.suffix, a.chunk_rows)
    elif a.cmd == "compare":
        compare_full(a.args[0], a.args[1])
    elif a.cmd == "configs":
        write_configs()
