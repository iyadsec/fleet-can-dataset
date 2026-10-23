"""P1 limited benign-only Isolation Forest sanity check (no attack data, no detection experiment).

Implementation used unchanged: detection/model.py + detection/features.py + detection/split.py (the repository's
audited reproduction of iyadsec/fleet-can-ids @ 4f1ebf3, documented in docs/m7_local_if_protocol.md):
  * one IsolationForest per vehicle profile (A/B/C), n_estimators=200, contamination='auto', random_state=42, no scaling;
  * input = exactly the 24 descriptor features (detection.features.model_matrix allow-list), fillna(0) -> float32;
  * score = -decision_function, normalised to [0, 1] by the empirical CDF of the model's own benign training scores;
  * session-level split (detection.split.build_split, seed 42): per S0 configuration 3 train / 1 validation / 1 test.

Threshold methodology. The original P1 detection threshold ('FPR<=5% -> highest recall' on validation) REQUIRES
labelled attack windows in the validation partition; it is therefore NOT computed here (documented dependency, needs
approval of a leakage-free pre-evaluation protocol). The original P2 fleet-pipeline evidence constants
(weak 0.55, strong 0.80; fixed, label-free) are applied instead -- they are original constants, not a replacement.
Because the normalised score is a percentile of benign training scores, held-out benign windows drawn from the same
distribution exceed 0.80 with probability ~0.20 and 0.55 with probability ~0.45 BY CONSTRUCTION; the observed rates are
reported next to these exchangeability reference values.

Usage: .venv/bin/python -m research.benign_if [--repeat]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_schema import FEATURE_COLUMNS                     # noqa: E402
from detection.features import feature_frame                                # noqa: E402
from detection.model import IF_PARAMS, STRONG_FIXED, WEAK_FIXED, fit_profile_model, normalise   # noqa: E402
from detection.split import BENIGN_ALLOC, SPLIT_SEED, build_split           # noqa: E402
from research.common import RELEASE, WORK, atomic_csv, atomic_json, load_manifest   # noqa: E402


def load_session(sid: str) -> pd.DataFrame:
    d = pd.read_parquet(RELEASE / "data" / "descriptors" / sid / "descriptors.parquet")
    lab = pd.read_parquet(RELEASE / "data" / "ground_truth" / sid / "window_labels.parquet",
                          columns=["vehicle_id", "window_index", "window_class"])
    veh = pd.read_csv(RELEASE / "data" / "metadata" / sid / "vehicles.csv", dtype={"vehicle_id": str})
    d = d.merge(lab, on=["vehicle_id", "window_index"], validate="one_to_one")     # evaluation bookkeeping only
    d["profile"] = d.vehicle_id.map(dict(zip(veh.vehicle_id, veh.profile)))        # routing key only
    d["session_id"] = sid
    return d


def run(out_suffix: str = "") -> dict:
    recs = [r for r in load_manifest() if r["scenario"] == "S0"]
    runs = [{"session_id": r["session_id"], "config_id": r["configuration_id"], "repetition": r["repetition"],
             "attack": r["attack"]} for r in recs]
    split = build_split(runs, SPLIT_SEED)
    meta = {r["session_id"]: r for r in recs}
    data = pd.concat([load_session(s) for s in sorted(split)], ignore_index=True)
    data["split"] = data.session_id.map(split)
    data["N"] = data.session_id.map(lambda s: meta[s]["N"])
    data["route_group"] = data.session_id.map(lambda s: "G0" if meta[s]["phase"] == "G0" else "P1")
    assert (data.window_class == "benign_only").all(), "S0 sessions must contain benign windows only"
    models, rows = {}, []
    data["anomaly_score"] = np.nan
    for p in sorted(data.profile.unique()):
        tr = data[(data.split == "train") & (data.profile == p)]
        m = fit_profile_model(p, feature_frame(tr))
        models[p] = m
        sel = data.profile == p
        data.loc[sel, "anomaly_score"] = m.anomaly_score(feature_frame(data[sel]))
        rows.append({"profile": p, "train_windows": len(tr), "train_sessions": tr.session_id.nunique(),
                     "train_vehicles": tr.groupby(["session_id", "vehicle_id"]).ngroups})
    held = data[data.split.isin(["validation", "test"])].copy()
    held["strong"] = held.anomaly_score >= STRONG_FIXED
    held["weak_or_strong"] = held.anomaly_score >= WEAK_FIXED
    # reference: in-sample training windows (score = own percentile => ~0.20 / ~0.45)
    train = data[data.split == "train"]

    def summ(df, by, level):
        out = []
        groups = [((), df)] if not by else df.groupby(by)
        for k, g in groups:
            k = k if isinstance(k, tuple) else (k,)
            fpr_s = float(g.strong.mean())
            se = np.sqrt(fpr_s * (1 - fpr_s) / len(g)) if len(g) else np.nan
            vs = g.groupby(["session_id", "vehicle_id"]).strong.mean()
            out.append({"grouping": level, **{b: v for b, v in zip(by, k)}, "held_out_windows": len(g),
                        "held_out_sessions": g.session_id.nunique(), "vehicles": g.groupby(["session_id", "vehicle_id"]).ngroups,
                        "fpr_strong_ge_0_80": fpr_s, "fpr_strong_95ci_low": max(0.0, fpr_s - 1.96 * se),
                        "fpr_strong_95ci_high": min(1.0, fpr_s + 1.96 * se),
                        "fpr_weak_or_strong_ge_0_55": float(g.weak_or_strong.mean()),
                        "exchangeable_reference_strong": 1 - STRONG_FIXED, "exchangeable_reference_weak_or_strong": 1 - WEAK_FIXED,
                        "vehicle_fpr_strong_median": float(vs.median()), "vehicle_fpr_strong_max": float(vs.max()),
                        "mean_anomaly_score": float(g.anomaly_score.mean()), "median_anomaly_score": float(g.anomaly_score.median())})
        return out

    res = []
    res += summ(held, [], "pooled")
    res += summ(held, ["profile"], "profile")
    res += summ(held, ["N"], "fleet_size")
    res += summ(held, ["route_group"], "route_group")
    res += summ(held, ["profile", "route_group"], "profile_x_route_group")
    res += summ(held, ["split"], "split")
    res += summ(held, ["session_id"], "session")
    # position in trace (warm-up / ECU switch-on effects)
    held["window_position"] = pd.cut(held.window_index, [-1, 0, 4, 19, 10**9], labels=["first", "2-5", "6-20", ">20"])
    res += summ(held, ["window_position"], "window_position")
    tr_ref = {"grouping": "training_in_sample", "held_out_windows": len(train), "fpr_strong_ge_0_80": float((train.anomaly_score >= STRONG_FIXED).mean()),
              "fpr_weak_or_strong_ge_0_55": float((train.anomaly_score >= WEAK_FIXED).mean())}
    res.append(tr_ref)
    df = pd.DataFrame(res)
    # most deviating features of held-out strong windows (|z| vs the profile's training statistics)
    z_rows = []
    for p, m in models.items():
        tr = train[train.profile == p][FEATURE_COLUMNS]
        mu, sd = tr.mean(), tr.std(ddof=0).replace(0, np.nan)
        h = held[(held.profile == p)]
        for flag, g in (("strong", h[h.strong]), ("not_strong", h[~h.strong])):
            z = ((g[FEATURE_COLUMNS] - mu) / sd).abs()
            for f in FEATURE_COLUMNS:
                z_rows.append({"profile": p, "window_group": flag, "feature": f, "windows": len(g),
                               "mean_abs_z": float(z[f].mean()) if len(g) else None,
                               "training_constant": bool(np.isnan(sd[f]))})
    zdf = pd.DataFrame(z_rows)
    split_df = pd.DataFrame([{"session_id": s, "split": split[s], "N": meta[s]["N"], "phase": meta[s]["phase"],
                              "config_id": meta[s]["configuration_id"], "repetition": meta[s]["repetition"]} for s in sorted(split)])
    checks = {
        "split_session_disjoint": bool(held.session_id.isin(train.session_id).sum() == 0),
        "train_sessions_benign_S0_only": bool(all(meta[s]["scenario"] == "S0" for s in train.session_id.unique())),
        "attack_data_used": False,
        "if_params_equal_original": IF_PARAMS == {"n_estimators": 200, "contamination": "auto", "random_state": 42, "n_jobs": -1},
        "benign_alloc_original_m7": BENIGN_ALLOC == ("train", "train", "train", "validation", "test"),
        "thresholds_original_p2_constants": (WEAK_FIXED, STRONG_FIXED) == (0.55, 0.80),
        "p1_calibrated_threshold_computed": False,
        "model_input_columns": ";".join(FEATURE_COLUMNS),
        "profiles": rows,
    }
    WORK.mkdir(parents=True, exist_ok=True)
    atomic_csv(pa.Table.from_pandas(df, preserve_index=False), WORK / f"benign_fpr{out_suffix}.csv")
    atomic_csv(pa.Table.from_pandas(zdf, preserve_index=False), WORK / f"benign_feature_deviation{out_suffix}.csv")
    atomic_csv(pa.Table.from_pandas(split_df, preserve_index=False), WORK / f"benign_split{out_suffix}.csv")
    atomic_json(checks, WORK / f"benign_checks{out_suffix}.json")
    scores = held[["session_id", "vehicle_id", "window_index", "profile", "anomaly_score"]]
    scores.to_parquet(WORK / f"benign_scores{out_suffix}.parquet", index=False)
    return {"pooled": res[0], "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", action="store_true", help="run twice and compare (reproducibility)")
    a = ap.parse_args(argv)
    r1 = run()
    print(json.dumps(r1["pooled"], indent=1))
    if a.repeat:
        run("_repeat")
        s1 = pd.read_parquet(WORK / "benign_scores.parquet")
        s2 = pd.read_parquet(WORK / "benign_scores_repeat.parquet")
        d = float(np.abs(s1.anomaly_score.values - s2.anomaly_score.values).max())
        atomic_json({"max_abs_score_difference": d, "identical": d == 0.0}, WORK / "benign_repro.json")
        print("max score difference between two runs:", d)


if __name__ == "__main__":
    main()
