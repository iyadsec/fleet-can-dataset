"""Vectorised FLEET-GUARD 24-D descriptor (same definition as analysis/descriptor_extractor.py).

The feature definitions, ordering, window rule (start 0, stride 50, size 100, trailing partial window dropped),
missing-payload handling (bytes at index >= DLC are NaN, then nan-statistics, then fillna(0.0)) and the descriptor
timestamp (observable time of the window's last frame) are taken unchanged from the repository implementation, which
reproduces iyadsec/fleet-can-ids @ 4f1ebf3. This module only vectorises the per-window loop; equivalence with the
reference implementation is verified by research/descriptor_compat.py (validation/descriptor_compatibility.csv).

Inputs are detector-safe observations only (timestamp_ns, can_id, dlc, data) of ONE vehicle trace in observable
time order. Window labels are computed separately (window_labels) from ground truth and never enter this function.
"""

from __future__ import annotations

import warnings

import numpy as np

from analysis.descriptor_schema import FEATURE_COLUMNS
from research.common import STRIDE, WINDOW

HEXV = np.full(256, 255, dtype=np.uint8)
for _i, _ch in enumerate("0123456789ABCDEF"):
    HEXV[ord(_ch)] = _i
    HEXV[ord(_ch.lower())] = _i


def payload_matrix(data: np.ndarray, dlc: np.ndarray) -> np.ndarray:
    """(n, 8) float64 payload bytes, NaN at positions >= DLC (original dataset_loader convention)."""
    s = np.char.ljust(np.asarray(data, dtype="U16"), 16, "0")
    raw = np.frombuffer(s.astype("S16").tobytes(), dtype=np.uint8).reshape(-1, 16)
    v = HEXV[raw]
    if (v == 255).any():
        raise ValueError("non-hex payload character")
    b = (v[:, 0::2].astype(np.float64) * 16 + v[:, 1::2])
    b[np.arange(8)[None, :] >= np.asarray(dlc)[:, None]] = np.nan
    return b


def window_starts(n: int) -> np.ndarray:
    return np.arange(0, n - WINDOW + 1, STRIDE, dtype=np.int64) if n >= WINDOW else np.empty(0, np.int64)


def trace_descriptors(t_ns: np.ndarray, id_codes: np.ndarray, dlc: np.ndarray, pay: np.ndarray):
    """Features for every window of one trace. Returns (starts, features (nw, 24) float64, nan_filled (nw,))."""
    n = len(t_ns)
    starts = window_starts(n)
    nw = len(starts)
    feats = np.zeros((nw, 24), dtype=np.float64)
    if nw == 0:
        return starts, feats, np.zeros(0, np.int64)
    idx = starts[:, None] + np.arange(WINDOW)[None, :]
    feats[:, 0] = float(WINDOW)
    # CAN-ID distribution: run lengths of the sorted IDs per window
    s = np.sort(id_codes[idx], axis=1)
    new = np.ones_like(s, dtype=bool)
    new[:, 1:] = s[:, 1:] != s[:, :-1]
    pos = np.flatnonzero(new.ravel())
    lengths = np.diff(np.append(pos, nw * WINDOW))
    row_first_run = np.flatnonzero(pos % WINDOW == 0)    # index of each window's first run
    p = lengths / WINDOW
    feats[:, 1] = new.sum(axis=1)
    feats[:, 2] = -np.add.reduceat(p * np.log2(p + 1e-12), row_first_run)
    feats[:, 3] = np.maximum.reduceat(lengths, row_first_run) / WINDOW
    # inter-arrival times in seconds (original: np.diff(t_ns[s:e] / 1e9))
    t_s = t_ns / 1e9
    gaps = np.diff(t_s)[starts[:, None] + np.arange(WINDOW - 1)[None, :]]
    feats[:, 4] = np.mean(gaps, axis=1)
    feats[:, 5] = np.std(gaps, axis=1)
    d = dlc.astype(np.float64)[idx]
    feats[:, 6] = np.mean(d, axis=1)
    feats[:, 7] = np.std(d, axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        b = pay[idx]                                      # (nw, 100, 8)
        feats[:, 8:16] = np.nanmean(b, axis=1)
        feats[:, 16:24] = np.nanstd(b, axis=1)
    bad = ~np.isfinite(feats)
    nan_filled = bad.sum(axis=1)
    feats[bad] = 0.0                                      # original descriptor stage: fillna(0.0)
    return starts, feats, nan_filled


assert len(FEATURE_COLUMNS) == 24
