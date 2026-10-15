"""Checks on a session's descriptors.csv / descriptor_labels.csv (PASS / FAIL per check)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .descriptor_extractor import load_observations, window_frame_uids
from .descriptor_schema import (DESCRIPTOR_COLUMNS, FEATURE_COLUMNS, LABEL_COLUMNS, PROHIBITED_FIELDS,
                                WINDOW_SIZE, WINDOW_STRIDE)
from .windowing import window_starts


def validate_session(data: Path, session_id: str, desc: pd.DataFrame, labels: pd.DataFrame) -> list[dict]:
    checks = []

    def ok(cid, name, cond, detail=""):
        checks.append({"id": cid, "check": name, "status": "PASS" if cond else "FAIL", "detail": str(detail)})

    ok("D01", "descriptor columns = schema (24 features, no ground-truth columns)",
       list(desc.columns) == DESCRIPTOR_COLUMNS and not (set(desc.columns) & PROHIBITED_FIELDS)
       and len(FEATURE_COLUMNS) == 24, f"{len(FEATURE_COLUMNS)} features")
    X = desc[FEATURE_COLUMNS].to_numpy(dtype=float)
    ok("D02", "no NaN / Inf in features", bool(np.isfinite(X).all()) and len(desc) > 0,
       f"{len(desc)} descriptors; NaN filled as 0 in {int((desc.nan_filled_features > 0).sum())} (original fillna(0.0))")
    obs = load_observations(data / "raw" / session_id)
    members = window_frame_uids(obs)
    fr = obs.set_index("frame_uid")
    cross, sizes, order_bad = 0, set(), 0
    for d in desc.itertuples():
        uids = members[d.descriptor_id]
        w = fr.loc[uids]
        cross += int(w.vehicle_id.nunique() != 1 or w.session_id.nunique() != 1
                     or w.vehicle_id.iloc[0] != d.vehicle_id or w.session_id.iloc[0] != d.session_id)
        sizes.add(len(uids))
        order_bad += int(not w.tx_end_ns.astype("int64").is_monotonic_increasing)
    ok("D03", "every window has exactly 100 frames from ONE vehicle and ONE session, in time order",
       cross == 0 and sizes == {WINDOW_SIZE} and order_bad == 0, f"sizes={sorted(sizes)}, crossing={cross}")
    cnt_ok = True
    for (sid, vid), g in desc.groupby(["session_id", "vehicle_id"]):
        n = int(((obs.session_id == sid) & (obs.vehicle_id == vid)).sum())
        cnt_ok &= len(g) == len(window_starts(n)) and list(g.window_index) == list(range(len(g)))
    ok("D04", f"window count per vehicle = floor((n - {WINDOW_SIZE}) / {WINDOW_STRIDE}) + 1 (stride {WINDOW_STRIDE})", cnt_ok)
    mono = all(g.descriptor_timestamp_ns.is_monotonic_increasing and (g.window_end_ns >= g.window_start_ns).all()
               for _, g in desc.groupby("vehicle_id"))
    ts_ok = (desc.descriptor_timestamp_ns == desc.window_end_ns).all()
    ok("D05", "descriptor_timestamp = window_end_time; monotonic per vehicle", bool(mono and ts_ok))
    ok("D06", "frame_count feature = 100 for every window", bool((desc.frame_count == WINDOW_SIZE).all()))
    ok("D07", "labels file separate, one row per descriptor, no feature columns",
       list(labels.columns) == LABEL_COLUMNS and set(labels.descriptor_id) == set(desc.descriptor_id)
       and not (set(labels.columns) & set(FEATURE_COLUMNS)), f"{len(labels)} label rows")
    return checks
