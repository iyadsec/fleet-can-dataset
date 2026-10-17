"""Milestone 10 finalisation of fleetcan-sim-scale-1.0 (after scripts/run_m10_generation.py --all).

Raw-only component (user scope decision in M10): raw CAN data + separate ground truth + metadata/validation;
descriptors are produced later by the FLEET-GUARD pipeline and are not part of the component.

  validate  independent re-verification of all 835 published sessions against the frozen matrix: session_state
            COMPLETE with provenance, the runner's full verification (scripts/run_m10_generation.verify) on the
            published data, no missing / duplicate / unexpected FCS10_* sessions, exactly 5 repetitions per
            configuration -> data/final/fleetcan-sim-scale-1.0/validation_summary.json
  stats     descriptive dataset statistics only (no detector) -> data/final/fleetcan-sim-scale-1.0/statistics.json
  hash      manifest.jsonl + provenance/ + SHA-256 of every file of the component -> SHA256SUMS
  freeze    FREEZE.json and read-only on disk (component files only; fleetcan-sim-1.1 untouched)
  repro     regenerate representative sessions under REPRO ids (same configuration and seed, engineering only),
            compare all logical raw + ground-truth tables (scripts/compare_sessions.py), record, delete the duplicates
  baseline  full fleetcan-sim-1.1 integrity check

Usage: .venv/bin/python scripts/m10_finalize.py {validate|stats|hash|freeze|repro|baseline}
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import run_m10_generation as R  # noqa: E402
from session_state import session_state  # noqa: E402

DATA = ROOT / "data"
FINAL = DATA / "final" / "fleetcan-sim-scale-1.0"
SESSION_DIRS = ("raw", "ground_truth", "metadata", "validation", "generated_sumo", "m10_configs")   # raw-only component
REPRO = ["FCS10_N020_S0_none_K000_none_R1", "FCS10_N050_S1_replay_K000_isolated_R1",
         "FCS10_N100_S2_flooding_K000_independent_I05near_R1", "FCS10_N100_S3_replay_K100_simultaneous_R1",
         "FCS10_N100_S3_malfunction_K050_bounded_spread_10s_R1", "FCS10_N100_S3-mixed_flooding_K010_small_I05mixe_R1",
         "FCS10_G0_N010_S3_fuzzy_K005_small_R1"]


def matrix():
    doc, sha = R.load_matrix(R.EXPECTED_SHA)
    ss, cfg = R.ordered_sessions(doc)
    return doc, sha, ss, cfg


def write(name, obj):
    FINAL.mkdir(parents=True, exist_ok=True)
    (FINAL / name).write_text(json.dumps(obj, indent=1, default=str) + "\n")


def validate():
    doc, sha, ss, cfg = matrix()
    head = R.git_head_clean()[0]
    expected = {s["session_id"] for s in ss}
    found = defaultdict(set)
    for d in SESSION_DIRS:
        p = DATA / d
        if p.exists():
            for x in os.listdir(p):
                if x.startswith("FCS10_"):
                    found[d].add(x)
    unexpected = sorted(set().union(*found.values()) - expected) if found else []
    missing = {d: sorted(expected - found[d]) for d in SESSION_DIRS if expected - found[d]}
    derived = sorted(x for x in os.listdir(DATA / "analysis") if x.startswith("FCS10_"))
    per_cfg = Counter(s["config_id"] for s in ss if s["session_id"] in found["metadata"])
    bad = []
    commits = Counter()
    warnings = Counter()
    for i, s in enumerate(ss):
        c = cfg[s["config_id"]]
        cls, st = R.classify(s, c, sha)
        if cls != "COMPLETE":
            bad.append((s["session_id"], cls, st["state"]))
            continue
        meta = json.loads((DATA / "metadata" / s["session_id"] / "metadata.json").read_text())
        commits[meta["git"]["commit"]] += 1
        problems, info = R.verify(s, c, DATA, sha, meta["git"]["commit"])
        warnings.update(info.get("warnings", []))
        if problems:
            bad.append((s["session_id"], "VERIFY", problems))
        if i % 50 == 0:
            print(f"validated {i + 1}/{len(ss)}", flush=True)
    with open(R.LEDGER) as fh:
        led = list(csv.DictReader(fh))
    out = {"expected_sessions": len(expected), "published_complete": len(ss) - len([b for b in bad if b[1] != "VERIFY"]),
           "verified_ok": len(ss) - len(bad), "configurations": len(per_cfg), "repetitions_per_config": sorted(set(per_cfg.values())),
           "unexpected_FCS10": unexpected, "missing": missing, "unexpected_derived_analysis_dirs": derived, "problems": bad[:20], "generator_commits": dict(commits),
           "validator_warnings": dict(warnings), "ledger_status": dict(Counter(r["status"] for r in led)), "current_head": head,
           "verdict": "PASS" if not bad and not unexpected and not missing and not derived and len(per_cfg) == 167 and set(per_cfg.values()) == {5} else "FAIL",
           "time": R.now()}
    write("validation_summary.json", out)
    print(json.dumps({k: v for k, v in out.items() if k != "problems"}, indent=1))
    print("problems:", bad[:5])
    return out


def size(sid, d):
    p = DATA / d / sid
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def stats():
    import pyarrow.parquet as pq
    doc, sha, ss, cfg = matrix()
    with open(R.LEDGER) as fh:
        led = {r["session_id"]: r for r in csv.DictReader(fh)}
    agg = {k: defaultdict(lambda: defaultdict(float)) for k in ("N", "scenario", "attack", "timing", "phase")}
    tot = defaultdict(float)
    peak = {"generator_rss_MB": 0.0}
    for s in ss:
        c, sid = cfg[s["config_id"]], s["session_id"]
        fr = pq.ParquetFile(DATA / "raw" / sid / "can_frames.parquet").metadata.num_rows
        st = {"sessions": 1, "frames": fr, "simulated_h": c["duration_s"] / 3600, "vehicle_h": c["N"] * c["duration_s"] / 3600,
              "raw_GB": size(sid, "raw") / 1e9, "ground_truth_GB": size(sid, "ground_truth") / 1e9,
              "metadata_validation_GB": (size(sid, "metadata") + size(sid, "validation")) / 1e9,
              "generated_sumo_configs_GB": (size(sid, "generated_sumo") + size(sid, "m10_configs")) / 1e9,
              "generation_h": float(led[sid]["wall_time"] or 0) / 3600}
        st["total_GB"] = sum(v for k, v in st.items() if k.endswith("_GB"))
        timing = c["temporal_pattern"] if c["scenario"] != "S2" else c["independent_timing"]
        for key, val in (("N", c["N"]), ("scenario", c["scenario"]), ("attack", c["attack"]), ("timing", timing), ("phase", str(c["phase"]))):
            for k, v in st.items():
                agg[key][val][k] += v
        for k, v in st.items():
            tot[k] += v
        peak["generator_rss_MB"] = max(peak["generator_rss_MB"], float(led[sid]["peak_rss"] or 0))
    rnd = lambda d: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}
    out = {"total": rnd(tot), **{f"by_{k}": {str(a): rnd(b) for a, b in sorted(v.items(), key=lambda x: str(x[0]))} for k, v in agg.items()},
           "peak": peak, "free_disk_GB": round(R.free_gb(), 2), "note": "descriptive statistics only; no detector was run", "time": R.now()}
    write("statistics.json", out)
    print(json.dumps({"total": out["total"], "peak": peak, "free_disk_GB": out["free_disk_GB"]}, indent=1))
    return out


def sha256(p: Path) -> str:
    return R.sha256(p)


def component_files(ss):
    files = []
    for s in ss:
        for d in SESSION_DIRS:
            p = DATA / d / s["session_id"]
            files += sorted(f for f in p.rglob("*") if f.is_file())
    files += sorted(f for f in FINAL.rglob("*") if f.is_file() and f.name not in ("SHA256SUMS", "FREEZE.json"))
    return files


def hash_all():
    doc, sha, ss, cfg = matrix()
    FINAL.mkdir(parents=True, exist_ok=True)
    with open(FINAL / "manifest.jsonl", "w") as fh:
        for s in ss:
            c = cfg[s["config_id"]]
            meta = json.loads((DATA / "metadata" / s["session_id"] / "metadata.json").read_text())
            fh.write(json.dumps({"session_id": s["session_id"], "configuration_id": c["config_id"], "config_index": c["config_index"],
                                 "repetition": s["repetition"], "seed": s["seed"], "phase": str(c["phase"]), "purpose": c["purpose"],
                                 "dataset_version": R.COMPONENT, "matrix_id": R.MATRIX_ID, "matrix_sha256": sha,
                                 "schema_version": meta["versions"]["schema_version"], "generator_version": meta["versions"]["generator_version"],
                                 "descriptor_version": meta["versions"]["descriptor_version"], "git_commit": meta["git"]["commit"],
                                 "N": c["N"], "K": c["K"], "scenario": c["scenario"], "attack": c["attack"], "temporal_pattern": c["temporal_pattern"],
                                 "independent_timing": c["independent_timing"], "route_source": c["route_source"], "composition": s["composition"],
                                 "duration_s": c["duration_s"]}) + "\n")
    prov = FINAL / "provenance"
    prov.mkdir(exist_ok=True)
    for src in (R.MATRIX, ROOT / "results" / "m9_1_freeze" / "matrix_freeze_manifest.json", ROOT / "results" / "m9_1_freeze" / "expected_sessions.csv",
                ROOT / "config" / "profiles" / "profiles_abc.yaml", ROOT / "config" / "m8" / "fleets" / "fleet_20.yaml",
                ROOT / "config" / "m8" / "fleets" / "fleet_50.yaml", ROOT / "config" / "m8" / "fleets" / "fleet_100.yaml",
                ROOT / "config" / "final" / "fleets" / "fleet_10.yaml", ROOT / "src" / "version.py", R.LEDGER, R.EXECLOG):
        shutil.copy2(src, prov / src.name)
    for cp in sorted(R.RUN.glob("checkpoint_*.json")):
        shutil.copy2(cp, prov / cp.name)
    env = {"python": sys.version, "git_head": R.git_head_clean()[0],
           "sumo": subprocess.run(["sumo", "--version"], capture_output=True, text=True).stdout.splitlines()[0],
           "uname": " ".join(os.uname())}
    (prov / "environment.json").write_text(json.dumps(env, indent=1))
    files = component_files(ss)
    lines = [f"{sha256(f)}  {f.relative_to(DATA)}" for f in files]
    (FINAL / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    total = sum(f.stat().st_size for f in files)
    print(f"{len(lines)} files, {total} bytes -> {FINAL / 'SHA256SUMS'} ({sha256(FINAL / 'SHA256SUMS')})")
    return len(lines), total


def freeze():
    doc, sha, ss, cfg = matrix()
    val = json.loads((FINAL / "validation_summary.json").read_text())
    if val["verdict"] != "PASS":
        sys.exit("validation summary is not PASS; refusing to freeze")
    lines = (FINAL / "SHA256SUMS").read_text().splitlines()
    files = component_files(ss)
    marker = {"dataset_version": R.COMPONENT, "matrix_id": R.MATRIX_ID, "matrix_sha256": sha, "generator_version": "1.3.0",
              "schema_version": "1.0", "descriptor_version": "1.0", "configurations": 167, "sessions": len(ss),
              "files_hashed": len(lines), "total_bytes": sum(f.stat().st_size for f in files),
              "validation": val["verdict"], "generator_commits": val["generator_commits"],
              "frozen_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_head": R.git_head_clean()[0],
              "manifest_sha256": sha256(FINAL / "manifest.jsonl"), "sha256sums_sha256": sha256(FINAL / "SHA256SUMS"),
              "baseline_dependency": {"dataset_version": "fleetcan-sim-1.1", **R.BASE_HASHES},
              "status": "FROZEN (immutable; read-only on disk)"}
    (FINAL / "FREEZE.json").write_text(json.dumps(marker, indent=1))
    ro = ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    tops = [DATA / d / s["session_id"] for s in ss for d in SESSION_DIRS] + [FINAL]
    for top in tops:
        for f in [top, *top.rglob("*")]:
            f.chmod(f.stat().st_mode & ro)
    writable = sum(1 for top in tops for f in [top, *top.rglob("*")] if os.access(f, os.W_OK))
    marker["writable_after_freeze"] = writable
    print(json.dumps(marker, indent=1))
    return marker


def repro():
    from compare_sessions import compare
    doc, sha, ss, cfg = matrix()
    results = []
    for sid in REPRO:
        s = next(x for x in ss if x["session_id"] == sid)
        c = cfg[s["config_id"]]
        rid = "M10_REPRO_" + sid
        d = DATA / "m10_repro_configs" / rid
        d.mkdir(parents=True, exist_ok=True)
        exp = yaml.safe_load((DATA / "m10_configs" / sid / "experiment.yaml").read_text())
        exp["experiment"]["experiment_id"] = rid
        exp["output"]["root"] = "data"
        (d / "experiment.yaml").write_text(yaml.safe_dump(exp, sort_keys=False))
        g = subprocess.run([R.PY, "-m", "src.orchestrator", "--config", str(d / "experiment.yaml"), "--session-id", rid], cwd=ROOT,
                           capture_output=True, text=True)
        cmp = compare(DATA, sid, rid)
        ma, mb = (json.loads((DATA / "metadata" / x / "metadata.json").read_text()) for x in (sid, rid))
        same_meta = {"seed": ma["random_seed"] == mb["random_seed"],
                     "fleet": ma["fleet"]["vehicles"] == mb["fleet"]["vehicles"],
                     "route_sha256": ma["simulation"]["route_generation"]["route_sha256"] == mb["simulation"]["route_generation"]["route_sha256"],
                     "campaign_schedule": ma.get("campaign_schedule") == mb.get("campaign_schedule"),
                     "attacks_runtime": ma.get("attacks_runtime") == mb.get("attacks_runtime")}
        ok = g.returncode == 0 and all(str(v).startswith("IDENTICAL") for v in cmp.values()) and all(same_meta.values())
        results.append({"session_id": sid, "repro_id": rid, "generator_exit": g.returncode,
                        "tables": cmp, "metadata": same_meta, "identical": ok})
        print(sid, "IDENTICAL" if ok else "DIFFERENT", flush=True)
        for dd in ("raw", "ground_truth", "metadata", "validation", "analysis", "generated_sumo"):
            shutil.rmtree(DATA / dd / rid, ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(DATA / "m10_repro_configs", ignore_errors=True)
    out = {"sessions": results, "verdict": "PASS" if all(r["identical"] for r in results) else "FAIL",
           "excluded": "session ids, frame uids and host capture/wall-clock times (scripts/compare_sessions.py)", "time": R.now()}
    R.RUN.mkdir(parents=True, exist_ok=True)
    (R.RUN / "reproducibility.json").write_text(json.dumps(out, indent=1, default=str))
    print(out["verdict"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "stats", "hash", "freeze", "repro", "baseline"])
    a = ap.parse_args()
    if a.cmd == "baseline":
        print(json.dumps(R.baseline_check(), indent=1))
    else:
        {"validate": validate, "stats": stats, "hash": hash_all, "freeze": freeze, "repro": repro}[a.cmd]()


if __name__ == "__main__":
    main()
