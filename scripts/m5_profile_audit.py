"""Milestone 5 cross-profile descriptor audit and per-vehicle normalization audit (no detector, no labels
in any normalization parameter).

1. Benign descriptors of a heterogeneous S0 session (profiles A/B/C): per-feature medians per profile and a
   profile-dominance ratio = variance of the per-profile means / mean within-profile variance
   (same idea as compute_feature_dominance in the original FLEET-GUARD graph code, by profile).
2. Per-vehicle z-score normalization, parameters estimated ONLY from that vehicle's own descriptors with
   descriptor_timestamp < CALIBRATION_END_S (the protocol guarantees no attack starts before 10 s, so this
   period is attack-free by design, not by label). Dominance recomputed after normalization.
3. Attack effect after normalization (evaluation only: labels used to SELECT windows for reporting):
   median z-score of selected features in >= 50 % malicious windows, per attack type and profile.

Usage: .venv/bin/python scripts/m5_profile_audit.py [--data data] [--s0 SESSION] [--attacks SESSION ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_schema import FEATURE_COLUMNS  # noqa: E402
from src import storage  # noqa: E402

CALIBRATION_END_S = 8.0
EFFECT_FEATURES = ["can_id_entropy", "most_common_can_id_ratio", "unique_can_id_count", "mean_inter_arrival_time",
                   "std_inter_arrival_time", "mean_dlc", "byte_mean_0", "byte_std_0"]


def load(data: Path, sid: str) -> pd.DataFrame:
    d = storage.read_table(data / "analysis" / sid / "descriptors", as_str=False)
    lab = storage.read_table(data / "analysis" / sid / "descriptor_labels")
    meta = json.loads((data / "metadata" / sid / "metadata.json").read_text())
    prof = {v["vehicle_id"]: v["profile"].replace("profile_", "") for v in meta["fleet"]["vehicles"]}
    d = d.merge(lab[["descriptor_id", "malicious_frame_ratio", "attack_types"]], on="descriptor_id")
    d["profile"] = d.vehicle_id.map(prof)
    d["malicious_frame_ratio"] = d.malicious_frame_ratio.astype(float)
    return d


def dominance(df: pd.DataFrame, cols) -> pd.Series:
    out = {}
    for c in cols:
        g = df.groupby("profile")[c]
        within = g.var(ddof=0).mean()
        between = g.mean().var(ddof=0)
        out[c] = np.inf if within == 0 and between > 0 else (0.0 if between == 0 else between / within)
    return pd.Series(out)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Per-vehicle z-score from the vehicle's own calibration period (time-based, label-free)."""
    out = df.copy()
    for v, g in df.groupby("vehicle_id"):
        cal = g[g.descriptor_timestamp_ns < CALIBRATION_END_S * 1e9]
        mu, sd = cal[FEATURE_COLUMNS].mean(), cal[FEATURE_COLUMNS].std(ddof=0)
        out.loc[g.index, FEATURE_COLUMNS] = ((g[FEATURE_COLUMNS] - mu) / sd.replace(0, np.nan)).fillna(0.0).to_numpy()
    return out


def classify(raw_dom: float, const: bool) -> str:
    if const:
        return "constant (no information)"
    if raw_dom == np.inf or raw_dom > 10:
        return "strongly profile/identity dependent"
    if raw_dom > 1:
        return "moderately profile dependent"
    return "behaviour-comparable across profiles"


def table(df: pd.DataFrame) -> str:
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--s0", default="M5_BENCH_S0_3V_PQ_FAST")
    ap.add_argument("--attacks", nargs="*", default=["M5_BENCH_FLOODING_3V_PQ_FAST", "M5_BENCH_FUZZY_3V_PQ_FAST",
                                                     "M5_BENCH_MALFUNCTION_3V_PQ_FAST", "M5_BENCH_REPLAY_3V_PQ_FAST"])
    a = ap.parse_args()
    data = Path(a.data)
    s0 = load(data, a.s0)
    med = s0.groupby("profile")[FEATURE_COLUMNS].median().T
    dom_raw = dominance(s0, FEATURE_COLUMNS)
    dom_norm = dominance(normalise(s0), FEATURE_COLUMNS)
    rows = []
    for i, c in enumerate(FEATURE_COLUMNS):
        const = s0[c].nunique() == 1
        rows.append({"#": i, "feature": c, **{f"median_{p}": f"{med.loc[c, p]:.4g}" for p in med.columns},
                     "dominance_raw": "inf" if dom_raw[c] == np.inf else f"{dom_raw[c]:.3g}",
                     "dominance_after_vehicle_norm": f"{dom_norm[c]:.3g}", "class": classify(dom_raw[c], const)})
    print(f"## Cross-profile benign descriptors ({a.s0}: {len(s0)} descriptors; "
          f"{s0.groupby('profile').vehicle_id.nunique().to_dict()} vehicles per profile)\n")
    print(table(pd.DataFrame(rows)), "\n")

    eff = []
    for sid in a.attacks:
        if not storage.exists(data / "analysis" / sid / "descriptors"):
            continue
        d = normalise(load(data, sid))
        sel = d[d.malicious_frame_ratio >= 0.5]
        if sel.empty:                                    # e.g. malfunction never dominates a window
            sel = d[d.malicious_frame_ratio > 0]
        for (atype, prof), g in sel.groupby(["attack_types", "profile"]):
            eff.append({"attack": atype, "profile": prof, "windows": len(g),
                        **{f: f"{g[f].median():+.1f}" for f in EFFECT_FEATURES}})
    print("## Attack effect after per-vehicle normalization (median z-score vs the vehicle's own calibration period)\n")
    print(table(pd.DataFrame(eff)), "\n")


if __name__ == "__main__":
    main()
