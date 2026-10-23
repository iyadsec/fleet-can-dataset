"""Detector-safe data adapter for fleetcan-sim-research-1.0 (explicit allow-list; evaluation data never reachable).

Every detector-side component (CAN feature extraction, the 24-D descriptor, Isolation Forest inputs, graph node
features, graph edge construction, GraphSAGE inputs, clustering inputs, campaign decision logic) must obtain its data
through this module:

  load_observations(release, session_id)   -> per-vehicle observable traces (only data/raw_parquet is opened)
  sanitize_observations(df, mode)          -> allow-list enforcement on any observation table
  descriptors(obs)                          -> original 24-D descriptors + window keys/timestamps (no labels)
  model_input(descriptor_df)                -> exactly the 24 features, frozen order (detection.features)
  graph_inputs(descriptor_df)               -> what a fleet graph may use: vehicle key, descriptor timestamp, features

Roles of the observation columns:
  feature inputs   timestamp_ns (relative differences only), can_id, dlc, data
  grouping key     vehicle_id     (splits traces; never a numeric/categorical feature)
  ordering key     frame_index    (deterministic tie-break only; dropped before feature extraction)
Session identity is carried by the directory name only and is never a column of a detector table.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from analysis.descriptor_schema import FEATURE_COLUMNS
from detection.features import LeakageError, model_matrix

OBSERVATION_FEATURE_INPUTS = ("timestamp_ns", "can_id", "dlc", "data")
GROUPING_KEYS = ("vehicle_id",)
ORDERING_KEYS = ("frame_index",)
ALLOWED_OBSERVATION_COLUMNS = ORDERING_KEYS + ("timestamp_ns",) + GROUPING_KEYS + ("can_id", "dlc", "data")
DESCRIPTOR_KEY_COLUMNS = ("vehicle_id", "window_index", "first_frame_index", "last_frame_index", "window_start_ns",
                          "window_end_ns", "descriptor_timestamp_ns", "nan_filled_features")
GRAPH_INPUT_COLUMNS = ("vehicle_id", "window_index", "descriptor_timestamp_ns", *FEATURE_COLUMNS)

# Explicitly prohibited (documented reason in documentation/DATA_DICTIONARY.md). Anything not on the allow-list is
# rejected as well; this list makes the error message specific and is what the leakage tests inject.
PROHIBITED_FIELDS = frozenset({
    "scenario_id", "scenario", "session_id", "frame_uid", "capture_time_ns", "capture_offset_us", "sim_time_ns",
    "timestamp", "transmission_timestamp", "tx_start_ns", "tx_end_ns", "queue_delay_us", "frame_bits", "channel",
    "frame_label", "label", "is_attack", "malicious", "attack_id", "attack_ids", "attack_type", "attack_types",
    "attack_family", "campaign_id", "campaign_ids", "coordinated", "campaign_membership", "campaign_size",
    "delay_pattern", "temporal_pattern", "relative_delay_s", "vehicle_attack_start_ns", "vehicle_attack_end_ns",
    "configured_start_ns", "configured_end_ns", "start_ns", "end_ns", "frame_source", "message_name",
    "malicious_frame_count", "malicious_frame_ratio", "window_class", "label_majority_original", "label_any_malicious",
    "first_malicious_timestamp_ns", "configuration_id", "config_id", "config_index", "repetition", "seed",
    "random_seed", "phase", "route_group", "route_source", "route_loop", "route_start_edge", "profile",
    "vehicle_profile", "fleet_size", "N", "K", "switch_on_ns", "ecu_activation_ns",
})


def _check_columns(columns, allowed, mode: str) -> list[str]:
    cols = list(columns)
    prohibited = sorted(set(cols) & PROHIBITED_FIELDS)
    unknown = sorted(set(cols) - set(allowed) - PROHIBITED_FIELDS)
    if mode == "strict" and (prohibited or unknown):
        raise LeakageError(f"non-detector columns in detector input: prohibited={prohibited} unknown={unknown}")
    if mode not in ("strict", "exclude"):
        raise ValueError(mode)
    return [c for c in cols if c in allowed]


def sanitize_observations(df: pd.DataFrame, mode: str = "strict") -> pd.DataFrame:
    """strict: any column outside the allow-list raises LeakageError. exclude: such columns are dropped."""
    keep = _check_columns(df.columns, ALLOWED_OBSERVATION_COLUMNS, mode)
    missing = [c for c in ("timestamp_ns", "vehicle_id", "can_id", "dlc", "data") if c not in keep]
    if missing:
        raise LeakageError(f"observation table lacks required columns {missing}")
    return df.loc[:, keep]


def observation_path(release: Path, session_id: str) -> Path:
    p = Path(release) / "data" / "raw_parquet" / session_id / "can_frames.parquet"
    if any(part in ("ground_truth", "metadata") for part in p.parts):
        raise LeakageError("detector adapter may only open data/raw_parquet")
    return p


def load_observations(release: Path, session_id: str) -> pd.DataFrame:
    """Only the allow-listed columns are read from disk; the file contains nothing else by construction."""
    p = observation_path(release, session_id)
    schema_cols = pq.read_schema(p).names
    _check_columns(schema_cols, ALLOWED_OBSERVATION_COLUMNS, "strict")
    return pq.read_table(p, columns=list(ALLOWED_OBSERVATION_COLUMNS)).to_pandas()


def traces(obs: pd.DataFrame):
    """(vehicle_id, trace) in observable order: timestamp_ns, ties broken by frame_index (deterministic)."""
    obs = sanitize_observations(obs, "strict")
    sort_cols = ["vehicle_id", "timestamp_ns"] + (["frame_index"] if "frame_index" in obs.columns else [])
    obs = obs.sort_values(sort_cols, kind="stable")
    for vid, g in obs.groupby("vehicle_id", sort=True):
        yield vid, g


def descriptors(obs: pd.DataFrame) -> pd.DataFrame:
    """Original 24-D descriptors per vehicle trace, from observable columns only."""
    from research.descriptors import payload_matrix, trace_descriptors
    from research.common import WINDOW
    parts = []
    for vid, g in traces(obs):
        t = g.timestamp_ns.to_numpy(np.int64)
        ids = np.array([int(x, 16) for x in g.can_id], dtype=np.int64)
        dlc = g.dlc.to_numpy(np.int64)
        starts, f, nanf = trace_descriptors(t, ids, dlc, payload_matrix(g.data.to_numpy(), dlc))
        fi = g.frame_index.to_numpy(np.int64) if "frame_index" in g.columns else np.arange(len(g))
        parts.append(pd.DataFrame({
            "vehicle_id": vid, "window_index": np.arange(len(starts), dtype=np.int64),
            "first_frame_index": fi[starts], "last_frame_index": fi[starts + WINDOW - 1],
            "window_start_ns": t[starts], "window_end_ns": t[starts + WINDOW - 1],
            "descriptor_timestamp_ns": t[starts + WINDOW - 1], "nan_filled_features": nanf.astype(np.int64),
            **{c: f[:, j] for j, c in enumerate(FEATURE_COLUMNS)}}))
    cols = list(DESCRIPTOR_KEY_COLUMNS) + FEATURE_COLUMNS
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)


def model_input(descriptor_df: pd.DataFrame) -> np.ndarray:
    """Isolation Forest / clustering / GraphSAGE node-feature matrix: exactly the 24 features in frozen order."""
    _check_columns(descriptor_df.columns, set(DESCRIPTOR_KEY_COLUMNS) | set(FEATURE_COLUMNS), "strict")
    return model_matrix(descriptor_df.loc[:, FEATURE_COLUMNS])


def graph_inputs(descriptor_df: pd.DataFrame) -> pd.DataFrame:
    """What fleet-graph node/edge construction may see. Campaign/label/metadata columns raise LeakageError."""
    _check_columns(descriptor_df.columns, set(DESCRIPTOR_KEY_COLUMNS) | set(FEATURE_COLUMNS), "strict")
    return descriptor_df.loc[:, list(GRAPH_INPUT_COLUMNS)].copy()


def temporal_edges(graph_df: pd.DataFrame, max_dt_s: float) -> pd.DataFrame:
    """Reference edge rule using observable descriptor timestamps only: connect windows of different vehicles whose
    descriptor timestamps differ by <= max_dt_s. (Illustrative leakage-test target; not a detector design.)"""
    g = graph_inputs(graph_df)[["vehicle_id", "window_index", "descriptor_timestamp_ns"]]
    m = g.merge(g, how="cross", suffixes=("_i", "_j"))
    m = m[(m.vehicle_id_i < m.vehicle_id_j)
          & ((m.descriptor_timestamp_ns_i - m.descriptor_timestamp_ns_j).abs() <= int(max_dt_s * 1e9))]
    return m.reset_index(drop=True)
