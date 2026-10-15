"""Ground truth, stored separately from the observed CAN frames.

frame_labels.csv  : one row per captured frame (join key frame_uid). Label derives only from the
                    frame's provenance (which component created it), never from detector output.
campaigns.csv     : one row per (campaign, vehicle). Built only from config/campaigns.yaml.
                    Empty (header only) for the benign scenario S0.
"""

from __future__ import annotations

LABEL_COLUMNS = ["frame_uid", "session_id", "frame_label", "attack_type", "campaign_id",
                 "attack_id", "frame_source", "message_name"]

CAMPAIGN_COLUMNS = ["campaign_id", "scenario_id", "session_id", "attack_type", "vehicle_id",
                    "campaign_start", "campaign_end", "vehicle_attack_start", "vehicle_attack_end",
                    "relative_delay", "coordinated", "delay_pattern"]

BENIGN_SOURCES = {"benign_generator"}


def frame_label_rows(label_seed_rows: list[dict]) -> list[dict]:
    rows = []
    for r in label_seed_rows:
        if r["frame_source"] not in BENIGN_SOURCES:
            raise ValueError(f"frame source {r['frame_source']} has no ground-truth mapping yet")
        rows.append({**r, "frame_label": "benign", "attack_type": "None",
                     "campaign_id": "None", "attack_id": "None"})
    return rows


def campaign_rows(campaigns: list[dict], scenario_id: str, session_id: str) -> list[dict]:
    if campaigns:
        raise NotImplementedError("campaigns are implemented after Milestone 1 approval")
    return []
