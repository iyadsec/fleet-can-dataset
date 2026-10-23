"""Release validation driver: leakage + temporal tests, dataset-wide leakage scan, descriptor compatibility, benign IF.

Usage: .venv/bin/python -m research.validate [--skip-compat] [--skip-benign]
Writes RELEASE/validation/{P1_metadata_leakage.csv, P1_benign_sanity_check.csv, descriptor_compatibility.csv,
test_results.csv} and RELEASE/results/benign_false_positive_statistics.csv.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_schema import FEATURE_COLUMNS                          # noqa: E402
from research import adapter                                                     # noqa: E402
from research.common import OBS_COLUMNS, RELEASE, ROOT, WORK, atomic_csv, load_manifest   # noqa: E402

VAL, RES = RELEASE / "validation", RELEASE / "results"


def wcsv(df, path):
    atomic_csv(pa.Table.from_pandas(df.astype({c: str for c in df.columns if df[c].dtype == object}), preserve_index=False), path)


def run_tests() -> pd.DataFrame:
    xml = WORK / "pytest_results.xml"
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}",
           "tests/test_metadata_leakage.py", "tests/test_temporal_consistency.py"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    (WORK / "pytest_output.txt").write_text(r.stdout + r.stderr)
    rows = []
    for tc in ET.parse(xml).getroot().iter("testcase"):
        outcome = "passed"
        detail = ""
        for tag in ("failure", "error", "skipped"):
            e = tc.find(tag)
            if e is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                detail = (e.get("message") or "")[:300]
        rows.append({"test_file": tc.get("classname", "").split(".")[-1], "test": tc.get("name"), "outcome": outcome,
                     "seconds": tc.get("time"), "detail": detail})
    df = pd.DataFrame(rows)
    wcsv(df, VAL / "test_results.csv")
    return df


def leakage_scan() -> pd.DataFrame:
    rows = []
    allowed_desc = set(adapter.DESCRIPTOR_KEY_COLUMNS) | set(FEATURE_COLUMNS)
    for r in sorted(load_manifest(), key=lambda r: r["session_id"]):
        sid = r["session_id"]
        obs_cols = pq.read_schema(RELEASE / "data" / "raw_parquet" / sid / "can_frames.parquet").names
        d_cols = pq.read_schema(RELEASE / "data" / "descriptors" / sid / "descriptors.parquet").names
        raw_files = sorted(p.name for p in (RELEASE / "data" / "raw_parquet" / sid).iterdir())
        csv_files = sorted(p.name for p in (RELEASE / "data" / "raw_csv" / sid).iterdir())
        rows.append({
            "check": "session_files", "session_id": sid,
            "observation_columns_exact_allow_list": obs_cols == OBS_COLUMNS,
            "observation_prohibited_columns": ";".join(sorted(set(obs_cols) & adapter.PROHIBITED_FIELDS)),
            "descriptor_columns_allowed": set(d_cols) <= allowed_desc,
            "descriptor_feature_order_frozen": [c for c in d_cols if c in FEATURE_COLUMNS] == FEATURE_COLUMNS,
            "descriptor_prohibited_columns": ";".join(sorted(set(d_cols) & adapter.PROHIBITED_FIELDS)),
            "observation_dir_only_frames": raw_files == ["can_frames.parquet"] and csv_files == ["can_frames.csv.gz"],
            "ground_truth_separate": (RELEASE / "data" / "ground_truth" / sid / "frame_labels.parquet").exists(),
            "metadata_separate": (RELEASE / "data" / "metadata" / sid / "session.json").exists()})
    df = pd.DataFrame(rows)
    df["pass"] = df.observation_columns_exact_allow_list & df.descriptor_columns_allowed & df.descriptor_feature_order_frozen \
        & (df.observation_prohibited_columns == "") & (df.descriptor_prohibited_columns == "") & df.observation_dir_only_frames \
        & df.ground_truth_separate & df.metadata_separate
    return df


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-compat", action="store_true")
    ap.add_argument("--skip-benign", action="store_true")
    a = ap.parse_args(argv)
    VAL.mkdir(parents=True, exist_ok=True)
    tests = run_tests()
    scan = leakage_scan()
    lk = tests[tests.test_file == "test_metadata_leakage"].assign(check="automated_test", session_id="")
    summary = pd.DataFrame([
        {"check": "dataset_scan_sessions_passing", "outcome": f"{int(scan['pass'].sum())}/{len(scan)}"},
        {"check": "automated_leakage_tests", "outcome": f"{int((lk.outcome == 'passed').sum())}/{len(lk)} passed"},
        {"check": "model_input_allow_list", "outcome": "detection.features.model_matrix: exactly 24 frozen features; any other column -> LeakageError"},
        {"check": "adapter", "outcome": "research/adapter.py: allow-list (frame_index, timestamp_ns, vehicle_id, can_id, dlc, data); unknown/prohibited columns rejected (strict) or excluded"}])
    p1 = pd.concat([summary, lk[["check", "test", "outcome", "detail"]], scan], ignore_index=True)
    wcsv(p1, VAL / "P1_metadata_leakage.csv")
    print(summary.to_string())
    print(tests.outcome.value_counts().to_string())
    if not a.skip_compat:
        r = subprocess.run([sys.executable, "-m", "research.descriptor_compat", "--per-config", "--workers", "2",
                            "--out", str(VAL / "descriptor_compatibility.csv")], cwd=ROOT, capture_output=True, text=True)
        print(r.stdout[-2000:], r.stderr[-2000:])
    if not a.skip_benign:
        r = subprocess.run([sys.executable, "-m", "research.benign_if", "--repeat"], cwd=ROOT, capture_output=True, text=True)
        print(r.stdout[-3000:], r.stderr[-2000:])
        fpr = pd.read_csv(WORK / "benign_fpr.csv")
        wcsv(fpr, RES / "benign_false_positive_statistics.csv")
        dev = pd.read_csv(WORK / "benign_feature_deviation.csv")
        wcsv(dev, RES / "benign_feature_deviation.csv")
        chk = json.loads((WORK / "benign_checks.json").read_text())
        rep = json.loads((WORK / "benign_repro.json").read_text())
        split = pd.read_csv(WORK / "benign_split.csv")
        rows = [{"check": k, "value": json.dumps(v) if isinstance(v, (list, dict)) else v} for k, v in chk.items()]
        rows.append({"check": "reproducible_two_runs_max_score_difference", "value": rep["max_abs_score_difference"]})
        rows += [{"check": "split", "value": f"{s.session_id}={s.split}"} for s in split.itertuples()]
        wcsv(pd.DataFrame(rows), VAL / "P1_benign_sanity_check.csv")


if __name__ == "__main__":
    main()
