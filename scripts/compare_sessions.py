"""Compare the deterministic (logical) content of two sessions, e.g. real-time vs fast mode, CSV vs Parquet,
or a re-run on newer code. Session ids, frame uids and capture/wall-clock times are excluded because they
legitimately differ; everything else must be identical.

Compared: raw frames (vehicle, generation/transmission times, queue delay, bits, ID, DLC, payload),
SUMO vehicle states, every ground-truth table, and (if both exist) descriptors (24 features + timestamps).
Usage: .venv/bin/python scripts/compare_sessions.py <session_a> <session_b> [--data data] [--frames-only]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import storage  # noqa: E402

LOGICAL_FRAME = ["vehicle_id", "sim_time_ns", "tx_start_ns", "tx_end_ns", "queue_delay_us", "frame_bits",
                 "can_id", "dlc", "payload", "timestamp", "transmission_timestamp"]
GT_TABLES = ["frame_labels", "dropped_frames", "attacks", "attack_frames", "replay_map", "campaigns",
             "campaign_membership", "campaign_overlap"]
VOLATILE = {"session_id", "frame_uid", "replay_frame_uid", "original_frame_uid"}


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop(columns=[c for c in df.columns if c in VOLATILE])
    return df.sort_values(list(df.columns)).reset_index(drop=True)


def logical_tables(data: Path, sid: str, frames_only: bool = False) -> dict[str, pd.DataFrame]:
    out = {"can_frames": _norm(storage.read_table(data / "raw" / sid / "can_frames")[LOGICAL_FRAME])}
    if frames_only:
        out["dropped_frames"] = _norm(storage.read_table(data / "ground_truth" / sid / "dropped_frames"))
        return out
    out["vehicle_states"] = _norm(storage.read_table(data / "raw" / sid / "vehicle_states"))
    for t in GT_TABLES:
        if storage.exists(data / "ground_truth" / sid / t):
            out[t] = _norm(storage.read_table(data / "ground_truth" / sid / t))
    return out


def compare(data: Path, a: str, b: str, frames_only: bool = False) -> dict[str, str]:
    ta, tb = logical_tables(data, a, frames_only), logical_tables(data, b, frames_only)
    res = {}
    for k in sorted(set(ta) | set(tb)):
        if k not in ta or k not in tb:
            res[k] = "MISSING"
            continue
        res[k] = f"IDENTICAL ({len(ta[k])} rows)" if ta[k].equals(tb[k]) else f"DIFFERENT ({len(ta[k])} vs {len(tb[k])})"
    da, db = data / "analysis" / a / "descriptors", data / "analysis" / b / "descriptors"
    if storage.exists(da) and storage.exists(db):
        from analysis.descriptor_schema import FEATURE_COLUMNS
        xa, xb = storage.read_table(da, as_str=False), storage.read_table(db, as_str=False)
        same_ts = list(xa.descriptor_timestamp_ns) == list(xb.descriptor_timestamp_ns) and \
            list(xa.window_start_ns) == list(xb.window_start_ns)
        diff = np.abs(xa[FEATURE_COLUMNS].to_numpy(float) - xb[FEATURE_COLUMNS].to_numpy(float)).max() if len(xa) == len(xb) else np.inf
        res["descriptors"] = (f"IDENTICAL ({len(xa)} descriptors, max |feature diff| = {diff:.3g})"
                              if same_ts and diff <= 1e-12 else f"DIFFERENT (max diff {diff}, timestamps equal={same_ts})")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--data", default="data")
    ap.add_argument("--frames-only", action="store_true", help="frames + drops only (e.g. sessions from older schemas)")
    a = ap.parse_args()
    res = compare(Path(a.data), a.a, a.b, a.frames_only)
    for k, v in res.items():
        print(f"{k:22s} {v}")
    sys.exit(0 if all(v.startswith("IDENTICAL") for v in res.values()) else 1)


if __name__ == "__main__":
    main()
