"""Descriptor schema: FLEET-GUARD behavioural features, identifiers, and the anti-leakage allow-list.

Source of the feature definitions (reproduced faithfully, not re-designed):
  repository  iyadsec/fleet-can-ids @ 4f1ebf3 (2026-09-12)
  file        src/features/feature_extractor.py  (BEHAVIOURAL_FEATURE_COLUMNS, extract_window_features)
  windowing   src/features/window_generator.py   (window_size=100, overlap=50 -> stride=50)
  config      configs/default.yaml               (features.window_size=100, overlap=50, stride=50)
  payload     src/data/dataset_loader.py         (byte0..byte7; bytes at index >= DLC are NaN)
  descriptor  src/features/descriptor_generator.py (24 features + separate anomaly_score; fillna(0.0))
"""

from __future__ import annotations

WINDOW_SIZE = 100          # frames per window (original FLEET-GUARD)
WINDOW_STRIDE = 50         # = window_size - overlap(50) (original FLEET-GUARD)

FEATURE_COLUMNS: list[str] = [
    "frame_count",
    "unique_can_id_count",
    "can_id_entropy",
    "most_common_can_id_ratio",
    "mean_inter_arrival_time",
    "std_inter_arrival_time",
    "mean_dlc",
    "std_dlc",
    *[f"byte_mean_{i}" for i in range(8)],
    *[f"byte_std_{i}" for i in range(8)],
]
assert len(FEATURE_COLUMNS) == 24          # 8 scalar features + 8 byte means + 8 byte stds

# index, name, definition, unit, calculation, source field (all over the n frames of one window)
FEATURE_TABLE: list[tuple] = [
    (0, "frame_count", "frames in the window", "frames", "n", "(window)"),
    (1, "unique_can_id_count", "distinct CAN IDs", "IDs", "|unique(can_id)|", "can_id"),
    (2, "can_id_entropy", "Shannon entropy of the CAN-ID distribution", "bits",
     "-sum p_k log2(p_k + 1e-12), p_k = count_k / n", "can_id"),
    (3, "most_common_can_id_ratio", "share of the most frequent CAN ID", "ratio", "max_k count_k / n", "can_id"),
    (4, "mean_inter_arrival_time", "mean gap between consecutive frames", "s", "mean(diff(t)), n-1 gaps", "observation time t"),
    (5, "std_inter_arrival_time", "population std of the gaps", "s", "std(diff(t), ddof=0)", "observation time t"),
    (6, "mean_dlc", "mean data length code", "bytes", "nanmean(dlc)", "dlc"),
    (7, "std_dlc", "population std of DLC", "bytes", "nanstd(dlc, ddof=0)", "dlc"),
] + [(8 + i, f"byte_mean_{i}", f"mean value of payload byte {i} over frames whose DLC > {i}", "0-255",
      f"nanmean(byte{i}); byte{i} = NaN when dlc <= {i}", "payload") for i in range(8)] \
  + [(16 + i, f"byte_std_{i}", f"population std of payload byte {i} over frames whose DLC > {i}", "0-255",
      f"nanstd(byte{i}, ddof=0)", "payload") for i in range(8)]

ID_COLUMNS: list[str] = [
    "descriptor_id", "session_id", "vehicle_id", "window_index",
    "first_frame_uid", "last_frame_uid", "n_frames",
    "window_start_time", "window_end_time", "descriptor_timestamp",
    "window_start_ns", "window_end_ns", "descriptor_timestamp_ns",
    "time_basis", "nan_filled_features",
]
DESCRIPTOR_COLUMNS: list[str] = ID_COLUMNS + FEATURE_COLUMNS

# Observable inputs. Everything else in the raw/ground-truth files is ignored by the extractor.
TIME_FIELDS = {"transmission": "tx_end_ns", "capture": "capture_time_ns"}
OBSERVATION_COLUMNS: list[str] = ["frame_uid", "session_id", "vehicle_id", "can_id", "dlc", "payload",
                                  "tx_end_ns", "capture_time_ns"]

# Ground-truth / hidden-timing fields that must never reach feature construction.
PROHIBITED_FIELDS: frozenset[str] = frozenset({
    "campaign_id", "attack_id", "attack_type", "frame_label", "label", "coordinated", "relative_delay",
    "relative_delay_s", "relative_delay_ns", "campaign_start", "campaign_end", "campaign_start_ns",
    "campaign_end_ns", "vehicle_attack_start", "vehicle_attack_end", "scenario_id", "frame_source",
    "message_name", "delay_pattern", "malicious", "is_attack",
    # simulator-internal timing that a bus observer does not see
    "sim_time_ns", "timestamp", "tx_start_ns", "queue_delay_us",
})

LABEL_COLUMNS: list[str] = [
    "descriptor_id", "session_id", "vehicle_id", "window_index", "n_frames",
    "malicious_frame_count", "malicious_frame_ratio", "window_class",
    "attack_ids", "campaign_ids", "attack_types", "n_attacks", "n_campaigns",
    "first_malicious_time_ns", "scenario_id",
]
