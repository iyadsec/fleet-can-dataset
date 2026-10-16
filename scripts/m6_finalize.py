"""Milestone 6 finalisation of fleetcan-sim-1.1 (run after scripts/m6_run_matrix.py and scripts/m6_audit.py).

  repro       regenerate one run per fleet size under a temporary REPRO_ session id with the same config and
              seed, run the frozen descriptor analysis, compare all logical tables + descriptors
              (scripts/compare_sessions.py), record the result, then delete the temporary duplicate
  provenance  snapshot protocol, profiles, DBCs, fleets, matrix, versions, git state and environment into
              data/final/fleetcan-sim-1.1/provenance/
  hash        SHA-256 of every file of the 610 sessions and of the final directory -> SHA256SUMS
  freeze      write FREEZE.json (versions, counts, SHA256SUMS digest) and make the dataset read-only

Usage: .venv/bin/python scripts/m6_finalize.py {repro|provenance|hash|freeze}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from compare_sessions import compare  # noqa: E402

DATA = ROOT / "data"
FINAL = DATA / "final" / "fleetcan-sim-1.1"
SPEC_P = ROOT / "config" / "final" / "matrix" / "matrix_spec.json"
SESSION_DIRS = ("raw", "ground_truth", "metadata", "validation", "analysis")
REPRO = ["FC11_N02_S3_fuzzy_s02_small_R1", "FC11_N05_S3-mixed_malfunction_s02_small_R3",
         "FC11_N10_S3_replay_s10_medium_R5"]


def runs():
    return json.loads(SPEC_P.read_text())["runs"]


def sh(*cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def repro():
    py = str(ROOT / ".venv" / "bin" / "python")
    by_sid = {r["session_id"]: r for r in runs()}
    results = []
    for sid in REPRO:
        r, tmp = by_sid[sid], "REPRO_" + sid
        assert not any((DATA / d / tmp).exists() for d in SESSION_DIRS), tmp
        g = sh(py, "-m", "src.orchestrator", "--config", r["experiment_config"], "--session-id", tmp)
        a = sh(py, "-m", "analysis.run_session", tmp)
        res = compare(DATA, sid, tmp)
        ok = g.returncode == 0 and a.returncode == 0 and all(v.startswith("IDENTICAL") for v in res.values())
        md_a = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
        md_b = json.loads((DATA / "metadata" / tmp / "metadata.json").read_text())
        results.append({"session_id": sid, "fleet_size": r["fleet_size"], "seed": r["seed"],
                        "regenerated_as": tmp, "generator_exit": g.returncode, "analysis_exit": a.returncode,
                        "seed_recorded": [md_a["random_seed"], md_b["random_seed"]], "tables": res,
                        "result": "IDENTICAL" if ok else "DIFFERENT"})
        print(json.dumps(results[-1]), flush=True)
        for d in SESSION_DIRS:  # the regenerated copy is not part of the dataset
            p = DATA / d / tmp
            if p.exists():
                shutil.rmtree(p)
    (FINAL / "reproducibility.json").write_text(json.dumps(results, indent=1))


def provenance():
    p = FINAL / "provenance"
    p.mkdir(parents=True, exist_ok=True)
    for src in ["docs/final_protocol.md", "docs/descriptors.md", "docs/dataset_schema.md", "docs/timing_model.md",
                "src/version.py", "config/profiles/profiles_abc.yaml", "config/final/matrix/matrix_spec.json"]:
        shutil.copy2(ROOT / src, p / Path(src).name)
    for d in ["config/final/fleets", "dbc"]:
        shutil.copytree(ROOT / d, p / Path(d).name, dirs_exist_ok=True)
    shutil.copytree(ROOT / "config/final/matrix", p / "matrix_configs", dirs_exist_ok=True)
    head = sh("git", "rev-parse", "HEAD").stdout.strip()
    (p / "git.json").write_text(json.dumps({
        "head": head, "dirty": bool(sh("git", "status", "--porcelain").stdout.strip()),
        "generator_commits_in_sessions": sorted({json.loads((DATA / "metadata" / r["session_id"] / "metadata.json")
                                                            .read_text())["git"]["commit"] for r in runs()}),
        "log": sh("git", "log", "--oneline", "-15").stdout.splitlines(),
        "generator_code_diff_since_freeze_3e732aa": sh("git", "diff", "--stat", "3e732aa", "HEAD", "--", "src", "analysis").stdout,
    }, indent=1))
    py = str(ROOT / ".venv" / "bin" / "python")
    (p / "environment.txt").write_text(
        sh(py, "--version").stdout + sh(py, "-c", "import sumo,traci;print('sumo', sumo.__version__ if hasattr(sumo,'__version__') else '')").stdout
        + sh("uname", "-a").stdout + "\n# pip freeze\n" + sh(py, "-m", "pip", "freeze").stdout)
    print(f"provenance -> {p}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_all():
    files = []
    for r in runs():
        for d in SESSION_DIRS:
            files += sorted(f for f in (DATA / d / r["session_id"]).rglob("*") if f.is_file())
    files += sorted(f for f in FINAL.rglob("*") if f.is_file() and f.name not in ("SHA256SUMS", "FREEZE.json"))
    lines = [f"{sha256(f)}  {f.relative_to(DATA)}" for f in files]
    (FINAL / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    print(f"{len(lines)} files hashed -> {FINAL / 'SHA256SUMS'} ({sha256(FINAL / 'SHA256SUMS')})")


def freeze():
    spec = json.loads(SPEC_P.read_text())
    audit = json.loads((FINAL / "audit.json").read_text())
    manifest = (FINAL / "manifest.jsonl").read_text().splitlines()
    marker = {"dataset_version": spec["dataset_version"], "generator_version": spec["generator_version"],
              "schema_version": spec["schema_version"], "descriptor_version": spec["descriptor_version"],
              "configurations": spec["configurations"], "sessions": len(manifest),
              "validation": audit["validation"], "frozen_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "git_head": sh("git", "rev-parse", "HEAD").stdout.strip(),
              "matrix_spec_sha256": sha256(SPEC_P), "manifest_sha256": sha256(FINAL / "manifest.jsonl"),
              "sha256sums_sha256": sha256(FINAL / "SHA256SUMS"), "status": "FROZEN (immutable; read-only on disk)"}
    (FINAL / "FREEZE.json").write_text(json.dumps(marker, indent=1))
    ro = ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    paths = [DATA / d / r["session_id"] for r in runs() for d in SESSION_DIRS] + [FINAL]
    for top in paths:
        for f in [top, *top.rglob("*")]:
            f.chmod(f.stat().st_mode & ro)
    print(json.dumps(marker, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["repro", "provenance", "hash", "freeze"])
    {"repro": repro, "provenance": provenance, "hash": hash_all, "freeze": freeze}[ap.parse_args().step]()
