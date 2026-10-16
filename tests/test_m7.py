"""Milestone 7: local Isolation Forest baseline — leakage, split, routing, scoring, thresholds, bands, frozen data."""

import json
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analysis.descriptor_schema import FEATURE_COLUMNS
from detection import model as M
from detection.data import DATA, FINAL, SPEC_PATH, sha256, verify_frozen
from detection.features import MODEL_FEATURES, LeakageError, feature_frame, model_matrix
from detection.pipeline import calibrate_thresholds, model_fingerprint, score_by_profile
from detection.split import build_split, check_split

RUNS = json.loads(SPEC_PATH.read_text())["runs"]


def _frame(n, rng, shift=0.0):
    return pd.DataFrame(rng.normal(shift, 1.0, size=(n, 24)), columns=list(MODEL_FEATURES))


# ---------------------------------------------------------------- features / leakage
def test_allow_list_is_exactly_the_frozen_24d_descriptor():
    assert MODEL_FEATURES == tuple(FEATURE_COLUMNS) and len(MODEL_FEATURES) == 24
    assert MODEL_FEATURES[:8] == ("frame_count", "unique_can_id_count", "can_id_entropy", "most_common_can_id_ratio",
                                  "mean_inter_arrival_time", "std_inter_arrival_time", "mean_dlc", "std_dlc")
    assert MODEL_FEATURES[8:16] == tuple(f"byte_mean_{i}" for i in range(8))
    assert MODEL_FEATURES[16:] == tuple(f"byte_std_{i}" for i in range(8))


@pytest.mark.parametrize("bad", ["profile", "vehicle_profile", "vehicle_id", "session_id", "attack", "attack_type", "campaign_id",
                                 "scenario_id", "scenario", "relative_delay_s", "malicious_frame_ratio", "window_class",
                                 "label", "window_start_ns", "split", "anomaly_score", "fleet_size"])
def test_prohibited_columns_rejected(bad):
    X = _frame(5, np.random.default_rng(0))
    X[bad] = 1
    with pytest.raises(LeakageError):
        model_matrix(X)


def test_wrong_order_or_missing_or_extra_feature_rejected():
    X = _frame(5, np.random.default_rng(0))
    with pytest.raises(LeakageError):
        model_matrix(X[list(reversed(MODEL_FEATURES))])
    with pytest.raises(LeakageError):
        model_matrix(X.drop(columns=["byte_std_7"]))
    with pytest.raises(LeakageError):
        model_matrix(X.assign(extra_feature=0.0))


def test_feature_frame_drops_everything_but_the_24_features():
    X = _frame(4, np.random.default_rng(1)).assign(profile="A", session_id="s", malicious_frame_count=3)
    F = feature_frame(X)
    assert tuple(F.columns) == MODEL_FEATURES
    m = model_matrix(F)
    assert m.shape == (4, 24) and m.dtype == np.float32


def test_original_preprocessing_fillna_zero_float32_no_scaling():
    X = _frame(3, np.random.default_rng(2))
    X.iloc[0, 5] = np.nan
    m = model_matrix(X)
    assert m[0, 5] == 0.0 and m.dtype == np.float32
    np.testing.assert_allclose(m[1], X.iloc[1].to_numpy(dtype=np.float32))


# ---------------------------------------------------------------- split
def test_split_session_level_and_counts():
    split = build_split(RUNS)
    check_split(split, RUNS)
    assert len(split) == 610
    vals = list(split.values())
    assert (vals.count("train"), vals.count("validation"), vals.count("test")) == (9, 241, 360)


def test_benign_only_training_sessions():
    split = build_split(RUNS)
    attack = {r["session_id"]: r["attack"] for r in RUNS}
    train = [s for s, v in split.items() if v == "train"]
    assert train and all(attack[s] == "none" for s in train)
    bad = dict(split)
    bad[next(s for s in split if attack[s] != "none")] = "train"
    with pytest.raises(AssertionError):
        check_split(bad, RUNS)


def test_no_session_overlap_between_splits():
    split = build_split(RUNS)
    sets = {k: {s for s, v in split.items() if v == k} for k in ("train", "validation", "test")}
    assert not (sets["train"] & sets["validation"]) and not (sets["train"] & sets["test"]) and not (sets["validation"] & sets["test"])
    assert sum(len(v) for v in sets.values()) == 610


def test_split_per_configuration_allocation():
    split = build_split(RUNS)
    by_cfg = {}
    for r in RUNS:
        by_cfg.setdefault((r["config_id"], r["attack"]), []).append(split[r["session_id"]])
    for (cid, attack), v in by_cfg.items():
        want = ["test", "train", "train", "train", "validation"] if attack == "none" else ["test", "test", "test", "validation", "validation"]
        assert sorted(v) == want, cid


def test_split_deterministic_and_seed_dependent():
    assert build_split(RUNS) == build_split(RUNS)
    assert build_split(RUNS, seed=42) != build_split(RUNS, seed=43)


# ---------------------------------------------------------------- model, scores, routing
def test_score_direction_higher_is_more_anomalous():
    rng = np.random.default_rng(3)
    pm = M.fit_profile_model("A", _frame(2000, rng))
    normal = _frame(50, rng)
    outlier = _frame(50, rng, shift=8.0)
    assert pm.raw_score(outlier).min() > np.median(pm.raw_score(normal))
    assert np.all(pm.anomaly_score(outlier) == 1.0)
    assert np.median(pm.anomaly_score(normal)) < 0.9


def test_normalisation_is_benign_training_empirical_cdf():
    ref = np.array([0.1, 0.2, 0.3, 0.4])
    np.testing.assert_allclose(M.normalise(np.array([0.05, 0.1, 0.25, 0.4, 9.0]), ref), [0.0, 0.25, 0.5, 1.0, 1.0])
    rng = np.random.default_rng(4)
    pm = M.fit_profile_model("B", _frame(1000, rng))
    s = pm.anomaly_score(_frame(1000, np.random.default_rng(4)))  # the training rows themselves
    assert s.min() >= 0.0 and s.max() == 1.0
    np.testing.assert_allclose(np.sort(s), np.arange(1, 1001) / 1000)  # ties-free: exact uniform ranks


def test_if_hyperparameters_match_original():
    assert M.IF_PARAMS == {"n_estimators": 200, "contamination": "auto", "random_state": 42, "n_jobs": -1}
    pm = M.fit_profile_model("C", _frame(300, np.random.default_rng(5)))
    assert len(pm.model.estimators_) == 200 and pm.model.max_samples_ == 256 and pm.scaler is None


def test_profile_specific_routing():
    rng = np.random.default_rng(6)
    models = {"A": M.fit_profile_model("A", _frame(500, rng)), "B": M.fit_profile_model("B", _frame(500, rng, shift=5.0))}
    d = pd.concat([_frame(20, rng).assign(profile="A"), _frame(20, rng, shift=5.0).assign(profile="B")], ignore_index=True)
    raw, score = score_by_profile(d, models)
    for p in ("A", "B"):
        m = (d.profile == p).to_numpy()
        np.testing.assert_array_equal(raw[m], models[p].raw_score(feature_frame(d[m])))
    assert score[(d.profile == "B").to_numpy()].mean() < 0.95          # B traffic is normal for IF-B
    assert models["A"].anomaly_score(feature_frame(d[d.profile == "B"])).mean() == 1.0   # but abnormal for IF-A
    with pytest.raises(KeyError):
        score_by_profile(d.assign(profile="C"), models)


def test_reproducible_models_and_scores():
    X = _frame(800, np.random.default_rng(7))
    a, b = M.fit_profile_model("A", X), M.fit_profile_model("A", X)
    assert model_fingerprint(a) == model_fingerprint(b)
    Y = _frame(100, np.random.default_rng(8))
    np.testing.assert_array_equal(a.anomaly_score(Y), b.anomaly_score(Y))


# ---------------------------------------------------------------- thresholds and bands
def _original_select(y, s, cap):
    """Literal port of the original loop (vehicle_level_evaluation._select_threshold_fpr_then_recall)."""
    best_t, best_recall = None, -1.0
    for t in M.threshold_candidates(s):
        pred = s >= t
        tp, fp = int((pred & (y == 1)).sum()), int((pred & (y == 0)).sum())
        fn, tn = int((~pred & (y == 1)).sum()), int((~pred & (y == 0)).sum())
        recall = tp / (tp + fn) if tp + fn else 0.0
        fpr = fp / (fp + tn) if fp + tn else 0.0
        if fpr <= cap + 1e-12 and recall > best_recall:
            best_recall, best_t = recall, float(t)
    return best_t


@pytest.mark.parametrize("seed", range(5))
def test_vectorised_threshold_selection_equals_original_loop(seed):
    rng = np.random.default_rng(seed)
    y = (rng.random(3000) < 0.3).astype(int)
    s = np.clip(rng.normal(0.5 + 0.3 * y, 0.2), 0, 1)
    s[rng.random(3000) < 0.1] = 1.0                       # ties at the saturation value, as in real scores
    for cap in (0.01, 0.05, 0.10):
        assert M.select_fpr_then_recall(y, s, cap) == _original_select(y, s, cap)


def test_calibration_fallback():
    y = np.array([0] * 10 + [1] * 10)
    s = np.ones(20)                                        # every candidate has FPR 1.0
    assert M.calibrate(y, s) == {"threshold": 0.99, "method": "FPR<=5% (fallback FPR<=10%)", "used_fallback": True}


def test_thresholds_use_validation_only_and_ignore_test():
    rng = np.random.default_rng(9)
    n = 4000
    desc = pd.DataFrame({"descriptor_id": [f"d{i}" for i in range(n)], "profile": rng.choice(list("ABC"), n),
                         "split": rng.choice(["train", "validation", "test"], n), "anomaly_score": rng.random(n)})
    labels = pd.DataFrame({"descriptor_id": desc.descriptor_id, "malicious_frame_count": (rng.random(n) < 0.3).astype(int) * 7,
                           "malicious_frame_ratio": rng.random(n)})
    base = calibrate_thresholds(desc, labels)
    test = (desc.split != "validation").to_numpy()
    d2, l2 = desc.copy(), labels.copy()
    d2.loc[test, "anomaly_score"] = 1.0 - d2.loc[test, "anomaly_score"]       # change every non-validation score
    l2.loc[test, "malicious_frame_count"] = 99                                  # and every non-validation label
    assert calibrate_thresholds(d2, l2) == base
    d3 = desc.copy()
    d3.loc[(desc.split == "validation").to_numpy(), "anomaly_score"] **= 3                       # validation changes do matter
    assert calibrate_thresholds(d3, labels) != base


def test_evidence_band_boundaries():
    s = np.array([0.0, 0.5499999, 0.55, 0.7999999, 0.80, 1.0])
    assert list(M.evidence_band(s, M.WEAK_FIXED, M.STRONG_FIXED)) == ["low", "low", "weak", "weak", "strong", "strong"]
    assert (M.WEAK_FIXED, M.STRONG_FIXED) == (0.55, 0.80)
    assert list(M.evidence_band(np.array([0.2, 0.9, 0.97]), np.array([0.9, 0.9, 0.9]), np.array([0.95, 0.95, 0.95]))) == ["low", "weak", "strong"]


# ---------------------------------------------------------------- frozen dataset
needs_data = pytest.mark.skipif(not (FINAL / "FREEZE.json").exists(), reason="frozen dataset not present")


@needs_data
def test_frozen_dataset_unchanged_digests_and_sample():
    r = verify_frozen(full=False)
    assert r["sha256sums_matches_freeze"] and r["manifest_matches_freeze"] and r["matrix_matches_freeze"]
    lines = (FINAL / "SHA256SUMS").read_text().splitlines()
    for line in random.Random(0).sample(lines, 40):
        digest, rel = line.split("  ", 1)
        assert sha256(DATA / rel) == digest, rel


@needs_data
def test_frozen_dataset_is_read_only():
    assert not os.access(FINAL, os.W_OK)
    assert not os.access(DATA / "analysis" / RUNS[0]["session_id"], os.W_OK)
    assert Path("results").resolve() != FINAL.resolve()
