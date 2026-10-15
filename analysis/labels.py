"""Evaluation-only labels for descriptors (descriptor_labels.csv).

Runs AFTER descriptors exist. Joins each window's frames with ground truth to report what the
window actually contains. Nothing here is ever fed back into descriptors.csv.

window_class: benign_only (no malicious frame) | malicious_only (all frames malicious) | mixed.
A window overlapping several attacks/campaigns lists all of them (';'-joined, sorted) instead of
forcing one label.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .descriptor_extractor import load_observations, window_frame_uids
from .descriptor_schema import LABEL_COLUMNS
from src import storage


def build_labels(descriptors: pd.DataFrame, data: Path, session_id: str) -> pd.DataFrame:
    gt = data / "ground_truth" / session_id
    lab = storage.read_table(gt / "frame_labels").set_index("frame_uid")
    obs = load_observations(data / "raw" / session_id)
    tx_end = dict(zip(obs.frame_uid, obs.tx_end_ns.astype("int64")))
    scenario = storage.read_table(data / "raw" / session_id / "can_frames", columns=["scenario_id"], nrows=1).scenario_id.iloc[0]
    basis = descriptors.time_basis.iloc[0] if len(descriptors) else "transmission"
    members = window_frame_uids(obs, basis)
    rows = []
    for d in descriptors.itertuples():
        uids = members[d.descriptor_id]
        w = lab.loc[uids]
        mal = w[w.frame_label == "malicious"]
        k = len(mal)
        attacks = sorted(set(mal.attack_id))
        camps = sorted(set(mal.campaign_id) - {"None"})
        rows.append({
            "descriptor_id": d.descriptor_id, "session_id": d.session_id, "vehicle_id": d.vehicle_id,
            "window_index": d.window_index, "n_frames": len(uids),
            "malicious_frame_count": k, "malicious_frame_ratio": f"{k / len(uids):.4f}",
            "window_class": "benign_only" if k == 0 else ("malicious_only" if k == len(uids) else "mixed"),
            "attack_ids": ";".join(attacks) or "None", "campaign_ids": ";".join(camps) or "None",
            "attack_types": ";".join(sorted(set(mal.attack_type))) or "None",
            "n_attacks": len(attacks), "n_campaigns": len(camps),
            "first_malicious_time_ns": str(min(tx_end[u] for u in mal.index)) if k else "",   # text: Parquet-safe
            "scenario_id": scenario})
    return pd.DataFrame(rows, columns=LABEL_COLUMNS)
