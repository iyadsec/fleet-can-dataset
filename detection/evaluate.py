"""M7 evaluation on the TEST split only (labels joined here, after scoring and thresholding).

Evaluation A: positive = malicious_frame_count > 0 (attack-containing), negative = benign_only.
Evaluation B: positive = malicious_frame_ratio >= 0.5 (original majority label), negative = every other window.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from detection.data import vehicle_states
from detection.features import MODEL_FEATURES

FAMILIES = ("flooding", "fuzzy", "malfunction", "replay")
PATTERN_DELAY = {"simultaneous": "0 s", "small": "0.5 s", "medium": "2 s", "large": "10 s",
                 "partial": "partial overlap (5 s steps, 10 s attacks)", "probe": "0.1 s (replay probe)"}
BANDS = ("low", "weak", "strong")


def binary_metrics(y: np.ndarray, pred: np.ndarray, score: np.ndarray) -> dict:
    """Original src/evaluation/vehicle_level_evaluation.py::_binary_metrics (+ TPR, counts)."""
    y, pred = np.asarray(y).astype(int), np.asarray(pred).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum()); tn = int(((pred == 0) & (y == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    roc = pr = float("nan")
    if len(np.unique(y)) >= 2:
        roc, pr = float(roc_auc_score(y, score)), float(average_precision_score(y, score))
    return {"n": int(len(y)), "positives": int(y.sum()), "negatives": int((y == 0).sum()), "pr_auc": pr, "roc_auc": roc,
            "precision": precision, "recall": recall, "tpr": recall, "f1": f1, "fpr": fpr,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def eval_frame(scores: pd.DataFrame, desc: pd.DataFrame, labels: pd.DataFrame, split: str = "test") -> pd.DataFrame:
    s = scores[scores.split == split]
    ctx = desc[["descriptor_id", "config_id", "fleet_size", "scenario", "attack", "campaign_size", "pattern",
                "window_start_ns", "window_end_ns", "switch_on_ns", *MODEL_FEATURES]]
    T = s.merge(ctx, on="descriptor_id").merge(labels, on="descriptor_id")
    T["label_A"] = (T.malicious_frame_count > 0).astype(int)
    T["label_B"] = (T.malicious_frame_ratio >= 0.5).astype(int)
    att = T[T.label_A == 1].groupby(["session_id", "vehicle_id"]).size()
    key = pd.MultiIndex.from_frame(T[["session_id", "vehicle_id"]])
    T["attacked_vehicle"] = key.isin(att.index)
    return T


def _metrics_block(T: pd.DataFrame, label: str, pred: str = "alert_det") -> dict:
    return binary_metrics(T[label].to_numpy(), T[pred].to_numpy(), T.anomaly_score.to_numpy())


def band_pct(g: pd.DataFrame, col: str) -> dict:
    n = len(g)
    return {f"pct_{b}": (100.0 * (g[col] == b).sum() / n) if n else float("nan") for b in BANDS}


def overall_metrics(T: pd.DataFrame) -> dict:
    res = {}
    for lab, name in (("label_A", "evaluation_A_attack_containing"), ("label_B", "evaluation_B_majority_malicious")):
        blk = {"pooled": _metrics_block(T, lab), "pooled_fixed_0.80_rule": _metrics_block(T, lab, "alert_fixed_080"),
               "profile": {p: _metrics_block(g, lab) for p, g in T.groupby("vehicle_profile")}, "attack": {}, "attack_profile": {}}
        for f in FAMILIES:
            g = T[T.attack == f]
            blk["attack"][f] = _metrics_block(g, lab)
            blk["attack"][f]["fixed_0.80_rule"] = _metrics_block(g, lab, "alert_fixed_080")
            for p, gp in g.groupby("vehicle_profile"):
                blk["attack_profile"][f"{f}/{p}"] = _metrics_block(gp, lab)
        res[name] = blk
    res["evaluation_B_families_without_positive_windows"] = [f for f in FAMILIES if int(T[(T.attack == f)].label_B.sum()) == 0]
    return res


def attack_profile_summary(T: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (f, p), g in T[T.attack.isin(FAMILIES)].groupby(["attack", "vehicle_profile"]):
        pos, neg = g[g.label_A == 1], g[g.label_A == 0]
        mA, mB = _metrics_block(g, "label_A"), _metrics_block(g, "label_B")
        rows.append({"attack": f, "profile": p, "test_sessions": g.session_id.nunique(),
                     "attacked_vehicle_traces": pos[["session_id", "vehicle_id"]].drop_duplicates().shape[0],
                     "descriptors": len(g), "attack_containing": len(pos), "majority_malicious": int(g.label_B.sum()),
                     "benign_only": len(neg), "pr_auc_A": mA["pr_auc"], "roc_auc_A": mA["roc_auc"], "precision_A": mA["precision"],
                     "recall_A": mA["recall"], "f1_A": mA["f1"], "fpr_A": mA["fpr"], "pr_auc_B": mB["pr_auc"],
                     "recall_B": mB["recall"], "precision_B": mB["precision"], "f1_B": mB["f1"],
                     "mean_score_attack": pos.anomaly_score.mean(), "median_score_attack": pos.anomaly_score.median(),
                     "share_score_eq_1_attack": (pos.anomaly_score >= 1.0).mean()})
    return pd.DataFrame(rows)


def evidence_band_summary(T: pd.DataFrame) -> pd.DataFrame:
    rows = []
    groups = [("benign_only (all test sessions)", T[T.label_A == 0])] + \
             [(f, T[(T.attack == f) & (T.label_A == 1)]) for f in FAMILIES] + \
             [(f"{f} majority-malicious", T[(T.attack == f) & (T.label_B == 1)]) for f in FAMILIES]
    for name, g in groups:
        for p in ("A", "B", "C", "all"):
            gp = g if p == "all" else g[g.vehicle_profile == p]
            for col, bset in (("band_fixed", "original fixed 0.55/0.80"), ("band_calibrated", "calibrated FPR<=10%/<=5%")):
                rows.append({"windows": name, "profile": p, "band_set": bset, "n": len(gp), **band_pct(gp, col)})
    return pd.DataFrame(rows)


def _local_summary(g: pd.DataFrame) -> dict:
    pos, neg = g[g.label_A == 1], g[g.label_A == 0]
    traces = g.groupby(["session_id", "vehicle_id"])
    att = traces.apply(lambda x: bool(((x.alert_det == 1) & (x.label_A == 1)).any()), include_groups=False)
    attacked = traces.attacked_vehicle.first()
    any_alert = traces.alert_det.max()
    return {"sessions": g.session_id.nunique(), "vehicle_traces": len(attacked), "attacked_traces": int(attacked.sum()),
            "descriptors": len(g), "attack_containing": len(pos), "benign_only": len(neg),
            "recall_A": pos.alert_det.mean() if len(pos) else float("nan"), "fpr_A": neg.alert_det.mean() if len(neg) else float("nan"),
            "mean_score_attack": pos.anomaly_score.mean() if len(pos) else float("nan"),
            "median_score_attack": pos.anomaly_score.median() if len(pos) else float("nan"),
            **{f"attack_{k}": v for k, v in band_pct(pos, "band_fixed").items()},
            **{f"attack_cal_{k}": v for k, v in band_pct(pos, "band_calibrated").items()},
            "attacked_trace_detection_rate": float(att[attacked].mean()) if attacked.any() else float("nan"),
            "nonattacked_trace_any_alert_rate": float(any_alert[~attacked].mean()) if (~attacked).any() else float("nan")}


def scenario_summary(T: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sc, g in T.groupby("scenario"):
        rows.append({"scenario": sc, "vehicles": "all", **_local_summary(g)})
        for role, gr in g.groupby("attacked_vehicle"):
            rows.append({"scenario": sc, "vehicles": "attacked" if role else "not attacked", **_local_summary(gr)})
        if sc != "S0":
            for f, gf in g.groupby("attack"):
                rows.append({"scenario": sc, "vehicles": f"all ({f})", **_local_summary(gf)})
    return pd.DataFrame(rows)


def campaign_size_summary(T: pd.DataFrame) -> pd.DataFrame:
    rows = []
    s3 = T[T.scenario == "S3"]
    for (f, cs), g in s3.groupby(["attack", "campaign_size"]):
        rows.append({"attack": f, "fleet_size": "all", "campaign_size": int(cs), **_local_summary(g)})
        for n, gn in g.groupby("fleet_size"):
            rows.append({"attack": f, "fleet_size": int(n), "campaign_size": int(cs), **_local_summary(gn)})
    s1 = T[T.scenario == "S1"]
    for f, g in s1.groupby("attack"):
        rows.append({"attack": f, "fleet_size": "all", "campaign_size": "1 (S1 isolated, reference)", **_local_summary(g)})
    return pd.DataFrame(rows)


def temporal_pattern_summary(T: pd.DataFrame) -> pd.DataFrame:
    rows = []
    s3 = T[T.scenario.isin(["S3", "S3-probe"])]
    for (f, pat), g in s3.groupby(["attack", "pattern"]):
        rows.append({"attack": f, "pattern": pat, "member_delay_step": PATTERN_DELAY[pat], **_local_summary(g)})
    return pd.DataFrame(rows)


def attack_start_time_summary(T: pd.DataFrame) -> pd.DataFrame:
    """S3 attacked vehicles grouped by when their own attack starts in the session (descriptive control for
    the temporal-pattern table: later members of staggered campaigns are attacked later in the drive)."""
    s3 = T[T.scenario == "S3"].copy()
    a0 = s3[s3.label_A == 1].groupby(["session_id", "vehicle_id"]).window_start_ns.min() / 1e9
    s3 = s3.join(a0.rename("vehicle_attack_start_s"), on=["session_id", "vehicle_id"])
    s3 = s3[s3.vehicle_attack_start_s.notna()]
    s3["attack_start_bin"] = pd.cut(s3.vehicle_attack_start_s, [0, 20, 40, 60, 1e9], labels=["10-20 s", "20-40 s", "40-60 s", ">=60 s"],
                                    right=False).astype(str)
    rows = []
    for (f, b), g in s3.groupby(["attack", "attack_start_bin"]):
        rows.append({"attack": f, "vehicle_attack_start": b, **_local_summary(g)})
    return pd.DataFrame(rows)


def score_distribution_summary(T: pd.DataFrame) -> pd.DataFrame:
    rows = []
    classes = [("benign_only", T[T.label_A == 0])] + [(f"{f} attack-containing", T[(T.attack == f) & (T.label_A == 1)]) for f in FAMILIES] \
        + [(f"{f} majority-malicious", T[(T.attack == f) & (T.label_B == 1)]) for f in FAMILIES]
    for name, g in classes:
        for p in ("A", "B", "C", "all"):
            x = (g if p == "all" else g[g.vehicle_profile == p]).anomaly_score
            q = x.quantile([0.05, 0.25, 0.5, 0.75, 0.95]) if len(x) else pd.Series([np.nan] * 5)
            rows.append({"class": name, "profile": p, "n": len(x), "mean": x.mean(), "std": x.std(), "min": x.min(),
                         "q05": q.iloc[0], "q25": q.iloc[1], "median": q.iloc[2], "q75": q.iloc[3], "q95": q.iloc[4],
                         "max": x.max(), "share_eq_1": (x >= 1.0).mean() if len(x) else np.nan})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ false positives
def _motion(T: pd.DataFrame) -> pd.DataFrame:
    """|speed change| and mean |acceleration| of the vehicle within each window (SUMO state, 10 ms)."""
    out = []
    for sid, g in T.groupby("session_id"):
        st = vehicle_states(sid)
        for vid, gv in g.groupby("vehicle_id"):
            sv = st[st.vehicle_id == vid].sort_values("sim_time_ns")
            t, v = sv.sim_time_ns.to_numpy(), sv.speed_mps.to_numpy()
            cum = np.concatenate([[0.0], np.cumsum(np.abs(sv.accel_mps2.to_numpy()))])
            a, b = gv.window_start_ns.to_numpy(), gv.window_end_ns.to_numpy()
            i = np.clip(np.searchsorted(t, a, side="left"), 0, len(t) - 1)
            j = np.clip(np.searchsorted(t, b, side="right"), 1, len(t))
            j = np.maximum(j, i + 1)
            out.append(pd.DataFrame({"descriptor_id": gv.descriptor_id.to_numpy(),
                                     "abs_speed_change_mps": np.abs(v[j - 1] - v[i]),
                                     "mean_abs_accel_mps2": (cum[j] - cum[i]) / (j - i),
                                     "speed_mps": v[i]}))
    return pd.concat(out, ignore_index=True)


def false_positive_analysis(T: pd.DataFrame, train_stats: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    B = T[T.label_A == 0].copy()
    B["fp_det"] = B.alert_det == 1
    B["weak_fixed"], B["strong_fixed"] = B.band_fixed == "weak", B.band_fixed == "strong"
    B["weak_cal"], B["strong_cal"] = B.band_calibrated == "weak", B.band_calibrated == "strong"
    t_on = (B.window_start_ns - B.switch_on_ns) / 1e9
    B["since_switch_on"] = pd.cut(t_on, [-1, 1, 2, 5, 10, 1e9], labels=["<1 s", "1-2 s", "2-5 s", "5-10 s", ">=10 s"], right=False).astype(str)
    # time within the session (benign training sessions S0 are 40 s long)
    B["session_time"] = pd.cut(B.window_start_ns / 1e9, [0, 10, 20, 30, 40, 60, 90, 1e9],
                               labels=["0-10 s", "10-20 s", "20-30 s", "30-40 s", "40-60 s", "60-90 s", ">=90 s"], right=False).astype(str)
    last_index = T.groupby(["session_id", "vehicle_id"]).window_index.transform("max")
    B["window_position"] = np.where(B.window_index == 0, "first window",
                                    np.where(B.window_index == last_index.loc[B.index], "last window", "interior"))
    # proximity to the vehicle's own attack-containing windows
    att = T[T.label_A == 1].groupby(["session_id", "vehicle_id"]).agg(a0=("window_start_ns", "min"), a1=("window_end_ns", "max"))
    B = B.join(att, on=["session_id", "vehicle_id"])
    after = (B.window_start_ns - B.a1) / 1e9
    B["attack_proximity"] = np.select(
        [B.a0.isna(), B.window_end_ns <= B.a0, after.between(0, 1, inclusive="left"), after >= 1],
        ["vehicle not attacked", "attacked vehicle, before attack", "attacked vehicle, <1 s after attack", "attacked vehicle, >=1 s after attack"],
        default="attacked vehicle, between attack windows")
    mot = _motion(B)
    B = B.merge(mot, on="descriptor_id", how="left")
    B["speed_change_bin"] = pd.qcut(B.abs_speed_change_mps.rank(method="first"), 4, labels=["Q1 (smallest)", "Q2", "Q3", "Q4 (largest)"]).astype(str)
    B["accel_bin"] = pd.qcut(B.mean_abs_accel_mps2.rank(method="first"), 4, labels=["Q1 (smallest)", "Q2", "Q3", "Q4 (largest)"]).astype(str)
    B["stationary"] = np.where(B.speed_mps < 0.1, "stationary at window start", "moving")
    # most deviating feature vs the profile's training statistics
    top = np.empty(len(B), dtype=object)
    for p, st in train_stats.items():
        m = (B.vehicle_profile == p).to_numpy()
        X = B.loc[m, list(MODEL_FEATURES)].fillna(0.0).to_numpy()
        mu, sd = st["mean"].to_numpy(), st["std"].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.where(sd > 0, np.abs(X - mu) / np.where(sd > 0, sd, 1), np.where(np.abs(X - mu) > 0, np.inf, 0.0))
        top[m] = np.array(MODEL_FEATURES)[np.argmax(z, axis=1)]
    B["top_deviating_feature"] = top
    rates = ["fp_det", "weak_fixed", "strong_fixed", "weak_cal", "strong_cal"]
    rows = []
    dims = ["vehicle_profile", "scenario", "fleet_size", "attack", "attacked_vehicle", "since_switch_on", "session_time", "window_position",
            "attack_proximity", "speed_change_bin", "accel_bin", "stationary", "top_deviating_feature"]
    rows.append({"dimension": "all", "value": "all", "benign_windows": len(B), **{f"{r}_rate": B[r].mean() for r in rates},
                 **{f"{r}_count": int(B[r].sum()) for r in rates}})
    for d in dims:
        for v, g in B.groupby(d):
            rows.append({"dimension": d, "value": str(v), "benign_windows": len(g), **{f"{r}_rate": g[r].mean() for r in rates},
                         **{f"{r}_count": int(g[r].sum()) for r in rates}})
    fp = pd.DataFrame(rows)
    tr = B.groupby(["session_id", "vehicle_id", "vehicle_profile", "scenario", "attack", "attacked_vehicle"], as_index=False).agg(
        benign_windows=("fp_det", "size"), fp_det=("fp_det", "sum"), strong_fixed=("strong_fixed", "sum"), weak_fixed=("weak_fixed", "sum"))
    tr["fp_det_rate"] = tr.fp_det / tr.benign_windows
    return fp, tr.sort_values(["fp_det", "strong_fixed"], ascending=False)


def train_feature_stats(desc: pd.DataFrame) -> dict[str, pd.DataFrame]:
    tr = desc[desc.split == "train"]
    return {p: pd.DataFrame({"mean": g[list(MODEL_FEATURES)].fillna(0.0).mean(), "std": g[list(MODEL_FEATURES)].fillna(0.0).std(ddof=0)})
            for p, g in tr.groupby("profile")}


def run_evaluation(out_dir: Path, desc: pd.DataFrame, labels: pd.DataFrame, scores: pd.DataFrame | None = None,
                   with_fp: bool = True) -> dict:
    out_dir = Path(out_dir)
    scores = scores if scores is not None else pd.read_parquet(out_dir / "descriptor_scores.parquet")
    T = eval_frame(scores, desc, labels, "test")
    metrics = overall_metrics(T)
    metrics["test_descriptors"] = len(T)
    metrics["test_sessions"] = int(T.session_id.nunique())
    (out_dir / "evaluation_metrics.json").write_text(json.dumps(metrics, indent=1, default=float))
    attack_profile_summary(T).to_csv(out_dir / "attack_profile_summary.csv", index=False)
    evidence_band_summary(T).to_csv(out_dir / "evidence_band_summary.csv", index=False)
    scenario_summary(T).to_csv(out_dir / "scenario_summary.csv", index=False)
    campaign_size_summary(T).to_csv(out_dir / "campaign_size_summary.csv", index=False)
    temporal_pattern_summary(T).to_csv(out_dir / "temporal_pattern_summary.csv", index=False)
    attack_start_time_summary(T).to_csv(out_dir / "attack_start_time_summary.csv", index=False)
    score_distribution_summary(T).to_csv(out_dir / "score_distribution_summary.csv", index=False)
    if with_fp:
        fp, traces = false_positive_analysis(T, train_feature_stats(desc))
        fp.to_csv(out_dir / "false_positive_summary.csv", index=False)
        traces.to_csv(out_dir / "false_positive_traces.csv", index=False)
    return metrics
