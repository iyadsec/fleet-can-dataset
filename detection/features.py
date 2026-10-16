"""Model-input allow-list: the frozen 24-D FLEET-GUARD descriptor and nothing else.

Every matrix that reaches an Isolation Forest is built by model_matrix(), which requires a frame whose
columns are exactly MODEL_FEATURES in frozen order. Identifiers, ground truth, configuration, timing,
profile and vehicle identity raise LeakageError.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analysis.descriptor_schema import FEATURE_COLUMNS, ID_COLUMNS, LABEL_COLUMNS, PROHIBITED_FIELDS

MODEL_FEATURES: tuple[str, ...] = tuple(FEATURE_COLUMNS)
assert len(MODEL_FEATURES) == 24

# Columns that exist somewhere in the M7 tables and must never be a model input.
FORBIDDEN_INPUTS: frozenset[str] = frozenset(PROHIBITED_FIELDS) | frozenset(ID_COLUMNS) | frozenset(LABEL_COLUMNS) | frozenset({
    "profile", "vehicle_profile", "fleet_size", "config_id", "configuration_id", "repetition", "scenario",
    "attack", "campaign_size", "pattern", "split", "anomaly_score", "raw_score", "switch_on_ns", "seed",
    "label_A", "label_B", "attacked_vehicle", "evidence_band", "predicted_alert",
})
assert not FORBIDDEN_INPUTS & set(MODEL_FEATURES)


class LeakageError(ValueError):
    """A model input contains something other than the 24 allowed descriptor features."""


def check_input_columns(columns) -> None:
    cols = list(columns)
    bad = sorted(set(cols) & FORBIDDEN_INPUTS)
    if bad:
        raise LeakageError(f"prohibited model input columns: {bad}")
    if tuple(cols) != MODEL_FEATURES:
        extra, missing = sorted(set(cols) - set(MODEL_FEATURES)), sorted(set(MODEL_FEATURES) - set(cols))
        raise LeakageError(f"model input must be exactly the 24 frozen features in order "
                           f"(extra={extra}, missing={missing}, order_ok={sorted(cols) == sorted(MODEL_FEATURES)})")


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Select the 24 features (frozen order) from a descriptor table; nothing else is carried."""
    missing = [c for c in MODEL_FEATURES if c not in df.columns]
    if missing:
        raise LeakageError(f"descriptor table lacks features {missing}")
    return df.loc[:, list(MODEL_FEATURES)]


def model_matrix(X: pd.DataFrame) -> np.ndarray:
    """Original preprocessing (src/models/vehicle_ids.py::_feature_matrix): fillna(0.0) -> float32, no scaling."""
    check_input_columns(X.columns)
    return X.astype("float64").fillna(0.0).to_numpy(dtype=np.float32)
