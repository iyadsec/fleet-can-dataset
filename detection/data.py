"""Read-only loading of the frozen fleetcan-sim-1.1 descriptors, labels and session context.

Descriptors and labels are returned as two separate tables (joined on descriptor_id only inside evaluation).
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from detection.features import MODEL_FEATURES
from src import storage

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FINAL = DATA / "final" / "fleetcan-sim-1.1"
SPEC_PATH = ROOT / "config" / "final" / "matrix" / "matrix_spec.json"
DATASET_VERSION = "fleetcan-sim-1.1"
SESSION_COLUMNS = ["config_id", "repetition", "fleet_size", "scenario", "attack", "campaign_size", "pattern", "duration_s"]
INT_COLS = ["window_index", "window_start_ns", "window_end_ns", "descriptor_timestamp_ns"]
FLOAT_COLS = ["window_start_time", "window_end_time", "descriptor_timestamp", *MODEL_FEATURES]


def load_runs() -> list[dict]:
    spec = json.loads(SPEC_PATH.read_text())
    assert spec["dataset_version"] == DATASET_VERSION
    return spec["runs"]


def _one(run: dict, data: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    sid = run["session_id"]
    md = json.loads((data / "metadata" / sid / "metadata.json").read_text())
    d = storage.read_table(data / "analysis" / sid / "descriptors")
    lab = storage.read_table(data / "analysis" / sid / "descriptor_labels",
                             columns=["descriptor_id", "malicious_frame_count", "malicious_frame_ratio", "window_class", "attack_types"])
    keep = ["descriptor_id", "session_id", "vehicle_id", *INT_COLS, *[c for c in FLOAT_COLS if c not in MODEL_FEATURES],
            *MODEL_FEATURES]
    d = d[keep].copy()
    for c in INT_COLS:
        d[c] = d[c].astype("int64")
    for c in FLOAT_COLS:
        d[c] = pd.to_numeric(d[c].replace("", np.nan), errors="raise").astype("float64")
    veh = {v["vehicle_id"]: v for v in md["fleet"]["vehicles"]}
    d["profile"] = d.vehicle_id.map(lambda v: veh[v]["profile"].replace("profile_", ""))
    d["switch_on_ns"] = d.vehicle_id.map(lambda v: int(veh[v]["switch_on_ns"])).astype("int64")
    for c in SESSION_COLUMNS:
        d[c] = run[c]
    lab["malicious_frame_count"] = lab.malicious_frame_count.astype("int64")
    lab["malicious_frame_ratio"] = lab.malicious_frame_ratio.astype("float64")
    return d, lab


def load_dataset(runs: list[dict] | None = None, data: Path = DATA, workers: int = 8) -> tuple[pd.DataFrame, pd.DataFrame]:
    runs = runs if runs is not None else load_runs()
    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(lambda r: _one(r, data), runs))
    desc = pd.concat([p[0] for p in parts], ignore_index=True)
    labels = pd.concat([p[1] for p in parts], ignore_index=True)
    assert desc.descriptor_id.is_unique and len(desc) == len(labels)
    assert set(desc.profile) <= {"A", "B", "C"}
    return desc, labels


def vehicle_states(sid: str, data: Path = DATA) -> pd.DataFrame:
    s = storage.read_table(data / "raw" / sid / "vehicle_states", columns=["sim_time_ns", "vehicle_id", "speed_mps", "accel_mps2"])
    s["sim_time_ns"] = s.sim_time_ns.astype("int64")
    s["speed_mps"] = s.speed_mps.astype("float64")
    s["accel_mps2"] = s.accel_mps2.astype("float64")
    return s


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_frozen(full: bool = True) -> dict:
    """Integrity of the frozen dataset: SHA256SUMS digest == FREEZE.json, and (full) every listed file."""
    freeze = json.loads((FINAL / "FREEZE.json").read_text())
    sums = FINAL / "SHA256SUMS"
    out = {"dataset_version": freeze["dataset_version"], "sessions": freeze["sessions"],
           "sha256sums_matches_freeze": sha256(sums) == freeze["sha256sums_sha256"],
           "manifest_matches_freeze": sha256(FINAL / "manifest.jsonl") == freeze["manifest_sha256"],
           "matrix_matches_freeze": sha256(SPEC_PATH) == freeze["matrix_spec_sha256"]}
    if full:
        bad = []
        lines = sums.read_text().splitlines()
        for line in lines:
            digest, rel = line.split("  ", 1)
            p = DATA / rel
            if not p.exists() or sha256(p) != digest:
                bad.append(rel)
        out.update({"files_checked": len(lines), "files_mismatched": len(bad), "mismatched_examples": bad[:5]})
    out["ok"] = all(v for k, v in out.items() if k.endswith(("_freeze",))) and out.get("files_mismatched", 0) == 0
    return out
