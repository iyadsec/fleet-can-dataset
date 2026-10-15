"""Behavioural descriptor extraction (analysis layer; consumes raw observations only).

    raw/<session>/can_frames.csv  --(observable columns only)-->  100-frame windows
    --> 24 FLEET-GUARD behavioural features + observable timestamps --> descriptors.csv

Anti-leakage: the extractor reads ONLY OBSERVATION_COLUMNS (usecols) and `build_descriptors`
refuses any DataFrame that contains a PROHIBITED_FIELDS column. Ground truth is joined later by
analysis/labels.py into a separate descriptor_labels.csv.

Descriptor timestamp: the original FLEET-GUARD windows have no timestamp. Here
    window_start_time    = observation time of the window's first frame
    window_end_time      = observation time of the window's last frame
    descriptor_timestamp = window_end_time      (the moment the descriptor can first exist)
Observation time ("time_basis") is, by default, the modelled bus time at which each frame was
complete on the bus (tx_end_ns = what a bus logger timestamps). "capture" uses the vCAN kernel
receive time instead (includes host/VM jitter). Generation time and configured campaign times are
never used: they are not observable on the bus.
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .descriptor_schema import (DESCRIPTOR_COLUMNS, FEATURE_COLUMNS, OBSERVATION_COLUMNS, PROHIBITED_FIELDS,
                                TIME_FIELDS, WINDOW_SIZE, WINDOW_STRIDE)
from .windowing import traces, window_starts
from src import storage

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LeakageError(ValueError):
    """Raised when ground-truth or hidden simulator fields reach feature construction."""


def assert_no_ground_truth(df: pd.DataFrame) -> None:
    bad = sorted(set(df.columns) & PROHIBITED_FIELDS)
    if bad:
        raise LeakageError(f"prohibited ground-truth/hidden fields passed to descriptor construction: {bad}")


def load_observations(session_dir: Path) -> pd.DataFrame:
    """Read only the observable columns of a session's raw CAN frames (CSV or Parquet)."""
    obs = storage.read_table(session_dir / "can_frames", columns=OBSERVATION_COLUMNS)[OBSERVATION_COLUMNS]
    for c in ("tx_end_ns", "capture_time_ns", "dlc"):       # numeric: times must sort numerically
        obs[c] = obs[c].astype("int64")
    return obs


def payload_bytes(payload: pd.Series, dlc: np.ndarray) -> np.ndarray:
    """n x 8 matrix of payload bytes; positions >= DLC are NaN (as in the original loader)."""
    out = np.full((len(payload), 8), np.nan)
    for r, (p, d) in enumerate(zip(payload, dlc)):
        k = min(int(d), len(p) // 2, 8)
        if k:
            out[r, :k] = np.frombuffer(bytes.fromhex(p[:2 * k]), dtype=np.uint8)
    return out


def _entropy(ids: np.ndarray) -> float:
    _, counts = np.unique(ids, return_counts=True)
    p = counts / counts.sum()
    return float(-np.sum(p * np.log2(p + 1e-12)))


def window_features(t_s: np.ndarray, can_ids: np.ndarray, dlc: np.ndarray, data: np.ndarray) -> dict:
    """The 24 FLEET-GUARD features for one window (t_s: observation times in seconds)."""
    n = len(can_ids)
    f = {"frame_count": float(n)}
    _, counts = np.unique(can_ids, return_counts=True)
    f["unique_can_id_count"] = float(len(counts))
    f["can_id_entropy"] = _entropy(can_ids)
    f["most_common_can_id_ratio"] = float(counts.max() / n)
    if n > 1:
        gaps = np.diff(t_s)
        gaps = gaps[np.isfinite(gaps)]
        f["mean_inter_arrival_time"] = float(np.mean(gaps)) if gaps.size else np.nan
        f["std_inter_arrival_time"] = float(np.std(gaps)) if gaps.size else np.nan
    else:
        f["mean_inter_arrival_time"] = f["std_inter_arrival_time"] = np.nan
    f["mean_dlc"] = float(np.nanmean(dlc))
    f["std_dlc"] = float(np.nanstd(dlc))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # all-NaN byte column -> NaN, as original
        means, stds = np.nanmean(data, axis=0), np.nanstd(data, axis=0)
    for i in range(8):
        f[f"byte_mean_{i}"] = float(means[i])
        f[f"byte_std_{i}"] = float(stds[i])
    return f


def build_descriptors(obs: pd.DataFrame, time_basis: str = "transmission", time_offset_ns: int = 0,
                      window_size: int = WINDOW_SIZE, stride: int = WINDOW_STRIDE) -> pd.DataFrame:
    """Windows + features from observable columns only. time_offset_ns converts the time basis to
    the session's observation clock origin (0 for transmission; the wall anchor for capture)."""
    assert_no_ground_truth(obs)
    tcol = TIME_FIELDS[time_basis]
    rows = []
    for sid, vid, tr in traces(obs, tcol):
        t_ns = tr[tcol].astype("int64").to_numpy() - int(time_offset_ns)
        dlc = tr["dlc"].astype(int).to_numpy()
        data = payload_bytes(tr["payload"], dlc)
        ids = tr["can_id"].astype(str).str.strip().str.upper().to_numpy()
        uids = tr["frame_uid"].to_numpy()
        for w, s in enumerate(window_starts(len(tr), window_size, stride)):
            e = s + window_size
            feats = window_features(t_ns[s:e] / 1e9, ids[s:e], dlc[s:e], data[s:e])
            nan_filled = sum(1 for c in FEATURE_COLUMNS if not np.isfinite(feats[c]))
            for c in FEATURE_COLUMNS:            # original descriptor stage: fillna(0.0)
                if not np.isfinite(feats[c]):
                    feats[c] = 0.0
            t0, t1 = int(t_ns[s]), int(t_ns[e - 1])
            rows.append({
                "descriptor_id": f"{sid}-{vid}-W{w:06d}", "session_id": sid, "vehicle_id": vid, "window_index": w,
                "first_frame_uid": uids[s], "last_frame_uid": uids[e - 1], "n_frames": window_size,
                "window_start_time": f"{t0 / 1e9:.9f}", "window_end_time": f"{t1 / 1e9:.9f}",
                "descriptor_timestamp": f"{t1 / 1e9:.9f}", "window_start_ns": t0, "window_end_ns": t1,
                "descriptor_timestamp_ns": t1, "time_basis": time_basis, "nan_filled_features": nan_filled,
                **feats})
    return pd.DataFrame(rows, columns=DESCRIPTOR_COLUMNS)


def window_frame_uids(obs: pd.DataFrame, time_basis: str = "transmission",
                      window_size: int = WINDOW_SIZE, stride: int = WINDOW_STRIDE) -> dict[str, list[str]]:
    """descriptor_id -> frame_uids in the window (used by the evaluation join and the validator)."""
    tcol = TIME_FIELDS[time_basis]
    out = {}
    for sid, vid, tr in traces(obs, tcol):
        uids = tr["frame_uid"].to_numpy()
        for w, s in enumerate(window_starts(len(tr), window_size, stride)):
            out[f"{sid}-{vid}-W{w:06d}"] = list(uids[s:s + window_size])
    return out


def time_offset(data: Path, session_id: str, time_basis: str) -> int:
    if time_basis == "transmission":
        return 0
    meta = json.loads((data / "metadata" / session_id / "metadata.json").read_text())
    anchor = meta["time_base"]["wall_anchor_ns"]
    if anchor is None:
        raise ValueError("capture time basis needs a real-time session (wall anchor)")
    return int(anchor)


def run(session_id: str, data: Path, out_root: Path, time_basis: str = "transmission") -> Path:
    obs = load_observations(data / "raw" / session_id)
    desc = build_descriptors(obs, time_basis, time_offset(data, session_id, time_basis))
    out = out_root / session_id
    out.mkdir(parents=True, exist_ok=True)
    return storage.write_frame(out / "descriptors", desc, storage.table_format(data / "raw" / session_id / "can_frames"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"))
    ap.add_argument("--out", default=None, help="default: <data>/analysis")
    ap.add_argument("--time-basis", choices=sorted(TIME_FIELDS), default="transmission")
    a = ap.parse_args(argv)
    data = Path(a.data)
    for sid in a.sessions:
        print(run(sid, data, Path(a.out) if a.out else data / "analysis", a.time_basis))


if __name__ == "__main__":
    main()
