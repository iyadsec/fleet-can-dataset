"""Temporal alignment of descriptors across vehicles.

Detector side (observable only):  delta_t(i, j) = |descriptor_timestamp_i - descriptor_timestamp_j|
Evaluation side (after the fact):  compare, per campaign member, three delays relative to the
earliest-starting member:
  configured_delay            from ground truth (campaign_membership.csv)
  first_malicious_frame_delay from the bus time of each vehicle's first malicious frame
  first_attack_descriptor_delay  from the first descriptor (by descriptor_timestamp) containing
                              >= 1 malicious frame of that member's attack
  first_majority_descriptor_delay  same, but the first descriptor whose malicious_frame_ratio >= 0.5
Configured campaign times are never used to compute detector-side delta_t.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src import storage


def delta_t(ts_i_ns, ts_j_ns) -> np.ndarray:
    """|t_i - t_j| in seconds for descriptor timestamps (ns); broadcasts."""
    return np.abs(np.asarray(ts_i_ns, dtype=np.int64) - np.asarray(ts_j_ns, dtype=np.int64)) / 1e9


def pairwise_delta_t(descriptors: pd.DataFrame) -> pd.DataFrame:
    """All cross-vehicle descriptor pairs with their delta_t (observable columns only)."""
    d = descriptors[["descriptor_id", "vehicle_id", "descriptor_timestamp_ns"]]
    m = d.merge(d, how="cross", suffixes=("_i", "_j"))
    m = m[m.vehicle_id_i < m.vehicle_id_j]
    m["delta_t_s"] = delta_t(m.descriptor_timestamp_ns_i, m.descriptor_timestamp_ns_j)
    return m.reset_index(drop=True)


def first_attack_descriptor(labels: pd.DataFrame, descriptors: pd.DataFrame, attack_id: str, min_ratio: float = 0.0):
    hit = labels[labels.attack_ids.str.split(";").apply(lambda xs: attack_id in xs)
                 & (labels.malicious_frame_ratio.astype(float) >= min_ratio)]
    if hit.empty:
        return None
    ts = descriptors.set_index("descriptor_id").loc[hit.descriptor_id, "descriptor_timestamp_ns"].astype("int64")
    return int(ts.min())


def campaign_alignment(data: Path, session_id: str, descriptors: pd.DataFrame, labels: pd.DataFrame,
                       first_mal: dict | None = None) -> pd.DataFrame:
    gt = data / "ground_truth" / session_id
    memb = storage.read_table(gt / "campaign_membership")
    camps = storage.read_table(gt / "campaigns")
    if memb.empty:
        return pd.DataFrame()
    # first malicious frame per attack, directly from frame-level ground truth and bus time
    # (the bounded pipeline passes it in, accumulated per vehicle group)
    if first_mal is None:
        fl = storage.read_table(gt / "frame_labels", columns=["frame_uid", "attack_id"])
        fr = storage.read_table(data / "raw" / session_id / "can_frames", columns=["frame_uid", "tx_end_ns"])
        first_mal = fl[fl.attack_id != "None"].merge(fr, on="frame_uid")
        first_mal = first_mal.assign(t=first_mal.tx_end_ns.astype("int64")).groupby("attack_id").t.min().to_dict()
    rows = []
    for cid, g in memb.groupby("campaign_id"):
        coordinated = camps.set_index("campaign_id").loc[cid, "coordinated"]
        recs = []
        for m in g.itertuples():
            fmt = first_mal.get(m.attack_id)
            recs.append((m.vehicle_id, int(m.vehicle_attack_start_ns), fmt,
                         first_attack_descriptor(labels, descriptors, m.attack_id),
                         first_attack_descriptor(labels, descriptors, m.attack_id, 0.5)))
        ref = min(recs, key=lambda r: (r[1], r[0]))
        for v, start, fmt, fdt, fmj in recs:
            rows.append({
                "session_id": session_id, "campaign_id": cid, "coordinated": coordinated, "vehicle_id": v,
                "reference_vehicle": ref[0],
                "configured_delay_s": (start - ref[1]) / 1e9,
                "first_malicious_frame_delay_s": None if fmt is None or ref[2] is None else (fmt - ref[2]) / 1e9,
                "first_attack_descriptor_delay_s": None if fdt is None or ref[3] is None else (fdt - ref[3]) / 1e9,
                "first_majority_descriptor_delay_s": None if fmj is None or ref[4] is None else (fmj - ref[4]) / 1e9,
                "first_malicious_frame_lag_s": None if fmt is None else (fmt - start) / 1e9,
                "first_attack_descriptor_lag_s": None if fdt is None else (fdt - start) / 1e9,
                "first_majority_descriptor_lag_s": None if fmj is None else (fmj - start) / 1e9,
            })
    return pd.DataFrame(rows)
