"""Sliding windows over one vehicle's frame stream (reproduces FLEET-GUARD window_generator.py).

Original rule (src/features/window_generator.py, generate_windows_for_trace):
    for start in range(0, n - window_size + 1, stride): window = frames[start:start + window_size]
i.e. windows start at frame 0, step by `stride` frames, and a trailing partial window is DROPPED.
The original builds windows per trace (one source file = one vehicle recording). Here a trace is
one (session_id, vehicle_id) stream ordered by its observation time, so a window can never mix
vehicles or experiment sessions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .descriptor_schema import WINDOW_SIZE, WINDOW_STRIDE


def window_starts(n_frames: int, window_size: int = WINDOW_SIZE, stride: int = WINDOW_STRIDE) -> np.ndarray:
    if window_size < 1 or stride < 1:
        raise ValueError("window_size and stride must be >= 1")
    if n_frames < window_size:
        return np.empty(0, dtype=np.int64)
    return np.arange(0, n_frames - window_size + 1, stride, dtype=np.int64)


def traces(frames: pd.DataFrame, time_col: str):
    """Yield (session_id, vehicle_id, trace) with each trace sorted by observation time (stable)."""
    for (sid, vid), g in frames.groupby(["session_id", "vehicle_id"], sort=True):
        yield sid, vid, g.sort_values(time_col, kind="stable").reset_index(drop=True)
