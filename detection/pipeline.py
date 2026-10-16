"""M7 pipeline: split -> leakage audit -> IF-A/B/C (benign S0 train) -> scores -> validation thresholds -> outputs.

Evaluation on the test split is done afterwards by detection.evaluate (it reads the saved scores).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from detection import model as M
from detection.data import DATASET_VERSION, ROOT, load_dataset, load_runs
from detection.features import FORBIDDEN_INPUTS, MODEL_FEATURES, LeakageError, feature_frame, model_matrix
from detection.split import SPLIT_SEED, build_split, check_split

PROFILES = ("A", "B", "C")
SCORE_COLUMNS = ["descriptor_id", "session_id", "vehicle_id", "vehicle_profile", "window_index", "window_start_time",
                 "window_end_time", "descriptor_timestamp", "raw_score", "anomaly_score", "split",
                 "band_fixed", "band_calibrated", "alert_det", "alert_fixed_080"]


def git_state() -> dict:
    def g(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": g("rev-parse", "HEAD"), "dirty": bool(g("status", "--porcelain", "--", "detection", "scripts", "analysis", "src"))}


def model_fingerprint(pm: M.ProfileModel) -> str:
    h = hashlib.sha256()
    for est, feats in zip(pm.model.estimators_, pm.model.estimators_features_):
        t = est.tree_
        for a in (t.feature, t.threshold, t.children_left, t.children_right, t.n_node_samples, feats):
            h.update(np.ascontiguousarray(a).tobytes())
    h.update(np.ascontiguousarray(pm.reference_raw).tobytes())
    if pm.scaler is not None:
        h.update(pm.scaler.mean_.tobytes())
        h.update(pm.scaler.scale_.tobytes())
    return h.hexdigest()


def split_counts(desc: pd.DataFrame) -> list[dict]:
    rows = []
    traces = desc.groupby(["split", "profile", "session_id", "vehicle_id"], as_index=False).agg(duration_s=("duration_s", "first"))
    for (sp, p), g in desc.groupby(["split", "profile"]):
        t = traces[(traces.split == sp) & (traces.profile == p)]
        rows.append({"split": sp, "profile": p, "sessions": int(g.session_id.nunique()), "descriptors": int(len(g)),
                     "vehicle_traces": int(len(t)), "vehicle_seconds": float(t.duration_s.sum()),
                     "scenarios": {k: int(v) for k, v in g.drop_duplicates("session_id").scenario.value_counts().sort_index().items()}})
    return rows


def leakage_audit(desc: pd.DataFrame, train_frames: dict[str, pd.DataFrame]) -> dict:
    raw = desc.loc[:, list(MODEL_FEATURES)]
    out = {"allowed_features": list(MODEL_FEATURES), "n_allowed": len(MODEL_FEATURES),
           "all_descriptors": {"shape": list(raw.shape), "nan_per_feature": {c: int(v) for c, v in raw.isna().sum().items() if v},
                               "inf_values": int(np.isinf(raw.to_numpy(dtype=np.float64)).sum())},
           "profiles": {}}
    for p, X in train_frames.items():
        m = model_matrix(X)
        top_share = X.apply(lambda c: c.value_counts(normalize=True, dropna=False).iloc[0])
        out["profiles"][p] = {
            "input_shape": list(m.shape), "input_dtype": str(m.dtype), "feature_names": list(X.columns),
            "columns_equal_allow_list": tuple(X.columns) == MODEL_FEATURES,
            "prohibited_columns_present": sorted(set(X.columns) & FORBIDDEN_INPUTS),
            "nan_before_fillna": int(X.isna().sum().sum()), "nan_after_fillna": int(np.isnan(m).sum()),
            "inf_values": int(np.isinf(m).sum()),
            "constant_features": [c for c in X.columns if X[c].nunique(dropna=False) == 1],
            "near_constant_features_top_value_share_ge_0.99": {c: round(float(v), 4) for c, v in top_share.items()
                                                                if v >= 0.99 and X[c].nunique(dropna=False) > 1},
        }
    # demonstrate rejection on the real frame
    probes = {}
    for bad in ("profile", "session_id", "malicious_frame_ratio", "scenario", "attack", "window_start_ns"):
        X = next(iter(train_frames.values())).copy()
        X[bad] = 0
        try:
            model_matrix(X)
            probes[bad] = "ACCEPTED (FAIL)"
        except LeakageError:
            probes[bad] = "rejected"
    out["rejection_probes"] = probes
    out["pass"] = all(v["columns_equal_allow_list"] and not v["prohibited_columns_present"] and v["nan_after_fillna"] == 0
                      and v["inf_values"] == 0 for v in out["profiles"].values()) and set(probes.values()) == {"rejected"}
    return out


def score_by_profile(desc: pd.DataFrame, models: dict[str, M.ProfileModel]) -> tuple[np.ndarray, np.ndarray]:
    """Route every descriptor to its own profile's model (profile is never a model input)."""
    unknown = set(desc.profile) - set(models)
    if unknown:
        raise KeyError(f"no model for profiles {sorted(unknown)}")
    raw = np.full(len(desc), np.nan)
    score = np.full(len(desc), np.nan)
    prof = desc.profile.to_numpy()
    for p, pm in models.items():
        idx = np.flatnonzero(prof == p)
        if idx.size:
            raw[idx] = pm.raw_score(feature_frame(desc.iloc[idx]))
            score[idx] = M.normalise(raw[idx], pm.reference_raw)
    return raw, score


def calibrate_thresholds(desc: pd.DataFrame, labels: pd.DataFrame) -> dict:
    """Original FPR<=5%/max-recall calibration per profile, using ONLY rows with split == 'validation'."""
    val = desc.loc[desc.split == "validation", ["descriptor_id", "profile", "anomaly_score"]].merge(
        labels[["descriptor_id", "malicious_frame_count", "malicious_frame_ratio"]], on="descriptor_id")
    yA = (val.malicious_frame_count > 0).astype(int).to_numpy()
    yB = (val.malicious_frame_ratio >= 0.5).astype(int).to_numpy()
    s = val.anomaly_score.to_numpy()
    th = {"data": "validation split only (test sessions never used)", "label_definition_primary": "A: malicious_frame_count > 0 vs benign_only",
          "procedure": "candidates = unique 300 quantiles (0.001..0.999) of validation scores; FPR <= cap, then highest recall; predict score >= t",
          "fixed_bands": {"weak": M.WEAK_FIXED, "strong": M.STRONG_FIXED}, "profiles": {}}
    for p in sorted(set(val.profile)):
        m = (val.profile == p).to_numpy()
        det = M.calibrate(yA[m], s[m])
        weak_cal = M.select_fpr_then_recall(yA[m], s[m], M.FALLBACK_FPR_CAP)
        rec, fpr = M.rates_at(yA[m], s[m], np.array([det["threshold"]]))
        th["profiles"][p] = {
            "theta_det": det["threshold"], "method": det["method"], "used_fallback": det["used_fallback"],
            "validation_recall_A": float(rec[0]), "validation_fpr_A": float(fpr[0]),
            "theta_weak_calibrated": float(weak_cal if weak_cal is not None else det["threshold"]),
            "theta_strong_calibrated": det["threshold"],
            "sensitivity_theta_label_B": M.calibrate(yB[m], s[m])["threshold"],
            "validation_descriptors": int(m.sum()), "validation_positives_A": int(yA[m].sum()),
            "validation_positives_B": int(yB[m].sum())}
    th["pooled_reference_theta_A"] = M.calibrate(yA, s)["threshold"]
    return th


def run(out_dir: Path, standardize: bool = False, desc: pd.DataFrame | None = None, labels: pd.DataFrame | None = None) -> dict:
    out_dir = Path(out_dir)
    (out_dir / "models").mkdir(parents=True, exist_ok=True)
    runs = load_runs()
    if desc is None or labels is None:
        desc, labels = load_dataset(runs)
    split = build_split(runs, SPLIT_SEED)
    check_split(split, runs)
    desc = desc.copy()
    desc["split"] = desc.session_id.map(split)

    # ---- split manifest
    by_split = {s: sorted(k for k, v in split.items() if v == s) for s in ("train", "validation", "test")}
    run_meta = {r["session_id"]: r for r in runs}
    profiles_of = desc.drop_duplicates(["session_id", "vehicle_id"]).groupby("session_id").profile.apply(lambda x: "".join(sorted(x)))
    manifest = {"dataset_version": DATASET_VERSION, "unit": "session", "seed": SPLIT_SEED,
                "rule": "per configuration, repetitions permuted by SeedSequence([seed, crc32(config_id)]); "
                        "S0: 3 train/1 validation/1 test; attack configurations: 0 train/2 validation/3 test",
                "sessions": {s: len(v) for s, v in by_split.items()},
                "counts_by_split_profile": split_counts(desc),
                "assignment": [{"session_id": s, "split": split[s], "config_id": run_meta[s]["config_id"],
                                "repetition": run_meta[s]["repetition"], "seed": run_meta[s]["seed"],
                                "scenario": run_meta[s]["scenario"], "attack": run_meta[s]["attack"],
                                "fleet_size": run_meta[s]["fleet_size"], "profiles": profiles_of[s]} for s in sorted(split)]}
    (out_dir / "split_manifest.json").write_text(json.dumps(manifest, indent=1))

    # ---- training (benign S0 train sessions only)
    train = desc[desc.split == "train"]
    lab_train = labels[labels.descriptor_id.isin(train.descriptor_id)]
    assert set(train.attack) == {"none"} and set(lab_train.window_class) == {"benign_only"}, "non-benign training data"
    train_frames = {p: feature_frame(train[train.profile == p]) for p in PROFILES}
    audit = leakage_audit(desc, train_frames)
    (out_dir / "leakage_audit.json").write_text(json.dumps(audit, indent=1))
    if not audit["pass"]:
        raise LeakageError("leakage audit failed; see leakage_audit.json")

    models: dict[str, M.ProfileModel] = {}
    cfg_models = {}
    for p in PROFILES:
        pm = M.fit_profile_model(p, train_frames[p], standardize=standardize)
        models[p] = pm
        path = out_dir / "models" / f"IF-{p}.joblib"
        joblib.dump({"profile": p, "model": pm.model, "reference_raw": pm.reference_raw, "scaler": pm.scaler,
                     "feature_columns": list(MODEL_FEATURES)}, path)
        cfg_models[f"IF-{p}"] = {"path": str(path.relative_to(out_dir)), "n_train_descriptors": pm.n_train,
                                 "n_train_vehicle_traces": int(train[train.profile == p][["session_id", "vehicle_id"]].drop_duplicates().shape[0]),
                                 "fingerprint_sha256": model_fingerprint(pm)}
    (out_dir / "training_descriptor_ids.json").write_text(json.dumps(
        {p: sorted(train[train.profile == p].descriptor_id) for p in PROFILES}))
    cfg = {"dataset_version": DATASET_VERSION, "git": git_state(), "variant": "standardized" if standardize else "raw_24d",
           "isolation_forest": {**M.IF_PARAMS, "max_samples": "auto", "max_features": 1.0, "bootstrap": False},
           "granularity": "one model per profile (A/B/C); profile used only for routing",
           "features": list(MODEL_FEATURES), "preprocessing": "float, fillna(0.0), float32" + (", StandardScaler fitted on training descriptors" if standardize else ", no scaling"),
           "score": {"raw": "-decision_function (higher = more anomalous)",
                     "anomaly_score": "searchsorted(sorted(train_raw), raw, 'right') / n_train, clipped to [0,1]"},
           "training_sessions": by_split["train"], "training_descriptor_ids_file": "training_descriptor_ids.json",
           "models": cfg_models, "split_seed": SPLIT_SEED}
    (out_dir / "model_config.json").write_text(json.dumps(cfg, indent=1))

    # ---- scores for every descriptor (routing by profile)
    raw, score = score_by_profile(desc, models)
    desc["raw_score"], desc["anomaly_score"] = raw, score

    # ---- thresholds: validation split only
    th = calibrate_thresholds(desc, labels)
    (out_dir / "threshold_calibration.json").write_text(json.dumps(th, indent=1))

    # ---- detector outputs
    tp = th["profiles"]
    prof = desc.profile.to_numpy()
    det_t = np.array([tp[p]["theta_det"] for p in prof])
    weak_c = np.array([tp[p]["theta_weak_calibrated"] for p in prof])
    desc["band_fixed"] = M.evidence_band(score, M.WEAK_FIXED, M.STRONG_FIXED)
    desc["band_calibrated"] = M.evidence_band(score, weak_c, det_t)
    desc["alert_det"] = (score >= det_t).astype("int8")
    desc["alert_fixed_080"] = (score >= M.STRONG_FIXED).astype("int8")
    out = desc.rename(columns={"profile": "vehicle_profile"})[SCORE_COLUMNS]
    out.to_parquet(out_dir / "descriptor_scores.parquet", index=False, compression="zstd")
    return {"desc": desc, "labels": labels, "thresholds": th, "models": models, "config": cfg, "audit": audit}
