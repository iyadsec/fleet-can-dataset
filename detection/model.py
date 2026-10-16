"""Original FLEET-GUARD local IDS (iyadsec/fleet-can-ids @ 4f1ebf3), reproduced per profile.

  fit      src/models/vehicle_ids.py::fit_self_supervised_isolation_forest
  score    src/models/vehicle_ids.py::score_self_supervised_isolation_forest + _normalised_anomaly_percentile
  thresh   src/evaluation/vehicle_level_evaluation.py::_threshold_candidates / _select_threshold_fpr_then_recall /
           _resolve_threshold_for_method("FPR<=5%")  (vectorised; identical selection)
  bands    src/models/vehicle_ids.py::generate_vehicle_anomaly_predictions (strong 0.80 / weak 0.55)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from detection.features import model_matrix

IF_PARAMS = {"n_estimators": 200, "contamination": "auto", "random_state": 42, "n_jobs": -1}
WEAK_FIXED, STRONG_FIXED = 0.55, 0.80          # original fleet-pipeline evidence constants
PRIMARY_FPR_CAP, FALLBACK_FPR_CAP = 0.05, 0.10  # original vehicle-level evaluator
N_CANDIDATE_QUANTILES = 300


@dataclass
class ProfileModel:
    """One benign-only Isolation Forest for one profile, with its benign training reference scores."""
    profile: str
    model: IsolationForest
    reference_raw: np.ndarray                  # -decision_function on the training descriptors (sorted)
    scaler: StandardScaler | None = None       # ablation only
    n_train: int = 0
    params: dict = field(default_factory=dict)

    def _matrix(self, X: pd.DataFrame) -> np.ndarray:
        m = model_matrix(X)
        return self.scaler.transform(m).astype(np.float32) if self.scaler is not None else m

    def raw_score(self, X: pd.DataFrame) -> np.ndarray:
        """Higher = more anomalous (original: -decision_function)."""
        return -self.model.decision_function(self._matrix(X))

    def anomaly_score(self, X: pd.DataFrame) -> np.ndarray:
        return normalise(self.raw_score(X), self.reference_raw)


def fit_profile_model(profile: str, X_train: pd.DataFrame, standardize: bool = False,
                      params: dict | None = None) -> ProfileModel:
    params = dict(IF_PARAMS if params is None else params)
    m = model_matrix(X_train)
    scaler = None
    if standardize:
        scaler = StandardScaler().fit(m)
        m = scaler.transform(m).astype(np.float32)
    model = IsolationForest(**params).fit(m)
    ref = np.sort(-model.decision_function(m).astype(np.float64))
    return ProfileModel(profile, model, ref, scaler, n_train=len(m), params=params)


def normalise(raw: np.ndarray, reference_raw: np.ndarray) -> np.ndarray:
    """Empirical CDF of the benign training raw scores: 0 = most normal, 1 = beyond every training window."""
    reference = np.sort(np.asarray(reference_raw, dtype=np.float64))
    if reference.size == 0:
        return np.zeros(len(raw), dtype=np.float64)
    ranks = np.searchsorted(reference, np.asarray(raw, dtype=np.float64), side="right")
    return np.clip(ranks / reference.size, 0.0, 1.0)


def threshold_candidates(scores: np.ndarray) -> np.ndarray:
    if scores.size == 0:
        return np.array([0.5])
    return np.unique(np.quantile(scores, np.linspace(0.001, 0.999, N_CANDIDATE_QUANTILES)))


def rates_at(y: np.ndarray, s: np.ndarray, thresholds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(recall, fpr) for predictions s >= t, for every t."""
    y = np.asarray(y).astype(int)
    pos, neg = np.sort(s[y == 1]), np.sort(s[y == 0])
    tp = pos.size - np.searchsorted(pos, thresholds, side="left")
    fp = neg.size - np.searchsorted(neg, thresholds, side="left")
    recall = tp / pos.size if pos.size else np.zeros(len(thresholds))
    fpr = fp / neg.size if neg.size else np.zeros(len(thresholds))
    return recall, fpr


def select_fpr_then_recall(y: np.ndarray, s: np.ndarray, max_fpr: float) -> float | None:
    """Among candidate thresholds with FPR <= max_fpr pick the highest recall (first = lowest such threshold)."""
    ts = threshold_candidates(np.asarray(s, dtype=np.float64))
    recall, fpr = rates_at(y, np.asarray(s, dtype=np.float64), ts)
    feasible = np.flatnonzero(fpr <= max_fpr + 1e-12)
    if feasible.size == 0:
        return None
    return float(ts[feasible[np.argmax(recall[feasible])]])


def calibrate(y: np.ndarray, s: np.ndarray) -> dict:
    """Original 'FPR<=5%' method incl. the FPR<=10% and 0.99 fallbacks."""
    t = select_fpr_then_recall(y, s, PRIMARY_FPR_CAP)
    if t is not None:
        return {"threshold": t, "method": "FPR<=5%", "used_fallback": False}
    t = select_fpr_then_recall(y, s, FALLBACK_FPR_CAP)
    return {"threshold": float(t if t is not None else 0.99), "method": "FPR<=5% (fallback FPR<=10%)", "used_fallback": True}


def evidence_band(s: np.ndarray, weak: np.ndarray | float, strong: np.ndarray | float) -> np.ndarray:
    """low: s < weak; weak: weak <= s < strong; strong: s >= strong (original >= semantics)."""
    s = np.asarray(s, dtype=np.float64)
    return np.where(s >= strong, "strong", np.where(s >= weak, "weak", "low"))
