"""Milestone 7: local Isolation Forest baseline on the frozen fleetcan-sim-1.1 (docs/m7_local_if_protocol.md).

  run         integrity check -> split -> IF-A/B/C -> scores -> validation thresholds -> test evaluation -> plots
              -> integrity check again                                  (results/m7_local_if/)
  ablation    same with per-profile StandardScaler fitted on training data (results/m7_local_if/ablation_standardized/)
  reproduce   re-run the full primary pipeline into a temporary directory and compare everything
  plots       regenerate plots from the saved results

Usage: .venv/bin/python scripts/m7_run.py {run|ablation|reproduce|plots} [--out results/m7_local_if]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from detection import evaluate, pipeline, plots  # noqa: E402
from detection.data import load_dataset, load_runs, verify_frozen  # noqa: E402
from detection.split import build_split  # noqa: E402

DEFAULT_OUT = ROOT / "results" / "m7_local_if"


def _load():
    t = time.time()
    desc, labels = load_dataset(load_runs())
    print(f"loaded {len(desc):,} descriptors / {len(labels):,} labels in {time.time() - t:.0f} s", flush=True)
    return desc, labels


def _with_split(desc):
    d = desc.copy()
    d["split"] = d.session_id.map(build_split(load_runs()))
    return d


def cmd_run(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pre = verify_frozen(full=True)
    print("integrity before:", pre, flush=True)
    if not pre["ok"]:
        raise SystemExit("frozen dataset integrity check failed - stop")
    desc, labels = _load()
    res = pipeline.run(out, desc=desc, labels=labels)
    print("thresholds:", json.dumps({p: {k: v for k, v in d.items() if k.startswith("theta")} for p, d in res["thresholds"]["profiles"].items()}), flush=True)
    m = evaluate.run_evaluation(out, res["desc"], labels)
    a = m["evaluation_A_attack_containing"]["pooled"]
    print(f"test pooled A: PR-AUC {a['pr_auc']:.4f} ROC {a['roc_auc']:.4f} P {a['precision']:.4f} R {a['recall']:.4f} "
          f"F1 {a['f1']:.4f} FPR {a['fpr']:.4f}", flush=True)
    T = evaluate.eval_frame(pd.read_parquet(out / "descriptor_scores.parquet"), res["desc"], labels)
    print("plots:", [p.name for p in plots.make_plots(out, T)], flush=True)
    post = verify_frozen(full=True)
    (out / "integrity_check.json").write_text(json.dumps({"before": pre, "after": post}, indent=1))
    print("integrity after:", post, flush=True)


def cmd_ablation(out: Path) -> None:
    ab = out / "ablation_standardized"
    desc, labels = _load()
    res = pipeline.run(ab, standardize=True, desc=desc, labels=labels)
    evaluate.run_evaluation(ab, res["desc"], labels, with_fp=False)
    prim_m = json.loads((out / "evaluation_metrics.json").read_text())
    abl_m = json.loads((ab / "evaluation_metrics.json").read_text())
    prim_t = json.loads((out / "threshold_calibration.json").read_text())
    abl_t = json.loads((ab / "threshold_calibration.json").read_text())
    rows = []
    for name, m, t in (("primary raw 24-D", prim_m, prim_t), ("ablation standardized", abl_m, abl_t)):
        A = m["evaluation_A_attack_containing"]
        for scope, blk in [("pooled", A["pooled"])] + [(f"profile {p}", A["profile"][p]) for p in "ABC"] + \
                [(f"attack {f}", A["attack"][f]) for f in evaluate.FAMILIES]:
            rows.append({"variant": name, "scope": scope, "pr_auc": blk["pr_auc"], "roc_auc": blk["roc_auc"], "precision": blk["precision"],
                         "recall": blk["recall"], "f1": blk["f1"], "fpr": blk["fpr"],
                         "theta_det": t["profiles"][scope[-1]]["theta_det"] if scope.startswith("profile") else ""})
        fprs = [A["profile"][p]["fpr"] for p in "ABC"]
        recs = [A["profile"][p]["recall"] for p in "ABC"]
        rows.append({"variant": name, "scope": "cross-profile spread (max-min)", "fpr": max(fprs) - min(fprs), "recall": max(recs) - min(recs),
                     "theta_det": max(t["profiles"][p]["theta_det"] for p in "ABC") - min(t["profiles"][p]["theta_det"] for p in "ABC")})
    pd.DataFrame(rows).to_csv(out / "ablation_comparison.csv", index=False)
    eb_p = pd.read_csv(out / "evidence_band_summary.csv").assign(variant="primary raw 24-D")
    eb_a = pd.read_csv(ab / "evidence_band_summary.csv").assign(variant="ablation standardized")
    pd.concat([eb_p, eb_a]).to_csv(out / "ablation_evidence_bands.csv", index=False)
    print(pd.DataFrame(rows).to_string(), flush=True)


def _cmp_json(a: Path, b: Path, drop=()) -> bool:
    ja, jb = json.loads(a.read_text()), json.loads(b.read_text())
    for k in drop:
        ja.pop(k, None); jb.pop(k, None)
    return ja == jb


def cmd_reproduce(out: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="m7_repro_"))
    try:
        desc, labels = _load()
        res = pipeline.run(tmp, desc=desc, labels=labels)
        evaluate.run_evaluation(tmp, res["desc"], labels, with_fp=False)
        s1, s2 = pd.read_parquet(out / "descriptor_scores.parquet"), pd.read_parquet(tmp / "descriptor_scores.parquet")
        same_order = bool((s1.descriptor_id.to_numpy() == s2.descriptor_id.to_numpy()).all())
        c1, c2 = json.loads((out / "model_config.json").read_text()), json.loads((tmp / "model_config.json").read_text())
        rep = {
            "run_1": str(out), "run_2": "temporary directory (deleted after comparison)",
            "split_manifest_identical": _cmp_json(out / "split_manifest.json", tmp / "split_manifest.json"),
            "model_config_identical_except_paths": {k: c1[k] == c2[k] for k in c1 if k != "models"},
            "model_fingerprints": {m: [c1["models"][m]["fingerprint_sha256"], c2["models"][m]["fingerprint_sha256"]] for m in c1["models"]},
            "model_fingerprints_identical": all(c1["models"][m]["fingerprint_sha256"] == c2["models"][m]["fingerprint_sha256"] for m in c1["models"]),
            "training_descriptor_ids_identical": _cmp_json(out / "training_descriptor_ids.json", tmp / "training_descriptor_ids.json"),
            "descriptor_order_identical": same_order,
            "max_abs_raw_score_difference": float(np.abs(s1.raw_score.to_numpy() - s2.raw_score.to_numpy()).max()),
            "max_abs_anomaly_score_difference": float(np.abs(s1.anomaly_score.to_numpy() - s2.anomaly_score.to_numpy()).max()),
            "thresholds_identical": _cmp_json(out / "threshold_calibration.json", tmp / "threshold_calibration.json"),
            "predictions_identical": {c: bool((s1[c].to_numpy() == s2[c].to_numpy()).all())
                                      for c in ("split", "band_fixed", "band_calibrated", "alert_det", "alert_fixed_080")},
            "evaluation_metrics_identical": _cmp_json(out / "evaluation_metrics.json", tmp / "evaluation_metrics.json"),
            "summary_csvs_identical": {f: pd.read_csv(out / f).equals(pd.read_csv(tmp / f)) for f in
                                       ("attack_profile_summary.csv", "evidence_band_summary.csv", "scenario_summary.csv",
                                        "campaign_size_summary.csv", "temporal_pattern_summary.csv", "score_distribution_summary.csv")},
        }
        rep["all_identical"] = bool(rep["split_manifest_identical"] and all(rep["model_config_identical_except_paths"].values())
                                    and rep["model_fingerprints_identical"] and rep["training_descriptor_ids_identical"] and same_order
                                    and rep["max_abs_anomaly_score_difference"] == 0.0 and rep["thresholds_identical"]
                                    and all(rep["predictions_identical"].values()) and rep["evaluation_metrics_identical"]
                                    and all(rep["summary_csvs_identical"].values()))
        (out / "reproducibility_report.json").write_text(json.dumps(rep, indent=1))
        print(json.dumps(rep, indent=1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_plots(out: Path) -> None:
    desc, labels = _load()
    T = evaluate.eval_frame(pd.read_parquet(out / "descriptor_scores.parquet"), _with_split(desc), labels)
    print([p.name for p in plots.make_plots(out, T)])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "ablation", "reproduce", "plots"])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()
    {"run": cmd_run, "ablation": cmd_ablation, "reproduce": cmd_reproduce, "plots": cmd_plots}[a.cmd](Path(a.out))
