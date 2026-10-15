"""Summaries of analysed sessions (Milestone 4 report tables). Evaluation only: labels are joined
here, after descriptors exist, and nothing is written back to descriptors.

Reporting-only derived quantities (NOT part of the 24-D descriptor):
  message_rate_hz = 1 / mean_inter_arrival_time
  burstiness      = std_inter_arrival_time / mean_inter_arrival_time   (as in the original graph view)

Usage: python -m analysis.summarize_descriptors [--data data] [--prefix M3_] [--delay-prefix M4_DELAY_]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .descriptor_extractor import PROJECT_ROOT
from src import storage

LIT = dict(keep_default_na=False, na_filter=False)
SANITY = ["message_rate_hz", "mean_inter_arrival_time", "burstiness", "can_id_entropy", "unique_can_id_count",
          "most_common_can_id_ratio", "mean_dlc", "std_dlc", "byte_std_0"]


def table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    return "\n".join(["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)])


def load(data: Path, prefix: str) -> pd.DataFrame:
    parts = []
    for d in sorted((data / "analysis").glob(f"{prefix}*")):
        if not storage.exists(d / "descriptors"):
            continue
        desc = storage.read_table(d / "descriptors", as_str=False)
        lab = storage.read_table(d / "descriptor_labels")
        parts.append(desc.merge(lab[["descriptor_id", "window_class", "malicious_frame_ratio", "attack_types",
                                     "campaign_ids", "scenario_id"]], on="descriptor_id"))
    df = pd.concat(parts, ignore_index=True)
    df["malicious_frame_ratio"] = df.malicious_frame_ratio.astype(float)
    df["message_rate_hz"] = 1.0 / df.mean_inter_arrival_time
    df["burstiness"] = df.std_inter_arrival_time / df.mean_inter_arrival_time
    return df


def sanity(df: pd.DataFrame) -> pd.DataFrame:
    groups = {"benign (S0 session)": df[df.scenario_id == "S0"],
              "benign windows of attack sessions": df[(df.scenario_id != "S0") & (df.window_class == "benign_only")]}
    for t in ["flooding", "fuzzy", "malfunction", "replay"]:
        groups[f"{t} (>=50% malicious)"] = df[(df.attack_types == t) & (df.malicious_frame_ratio >= 0.5)]
        groups[f"{t} (1-49% malicious)"] = df[(df.attack_types == t) & (df.malicious_frame_ratio > 0)
                                              & (df.malicious_frame_ratio < 0.5)]
    rows = []
    for name, g in groups.items():
        r = {"group": name, "windows": len(g)}
        for c in SANITY:
            if len(g):
                q = g[c].quantile([0.25, 0.5, 0.75]).to_numpy()
                r[c] = f"{q[1]:.4g} [{q[0]:.3g}-{q[2]:.3g}]"
            else:
                r[c] = "-"
        rows.append(r)
    return pd.DataFrame(rows)


def alignment(data: Path, prefix: str) -> pd.DataFrame:
    parts = [storage.read_table(d / "temporal_alignment", as_str=False) for d in sorted((data / "analysis").glob(f"{prefix}*"))
             if storage.exists(d / "temporal_alignment")]
    if not parts:
        return pd.DataFrame()
    a = pd.concat(parts, ignore_index=True)
    a["session"] = a.session_id.str.replace(r"_\d{8}T\d{6}Z$", "", regex=True)
    cols = ["session", "campaign_id", "coordinated", "vehicle_id", "configured_delay_s", "first_malicious_frame_delay_s",
            "first_attack_descriptor_delay_s", "first_majority_descriptor_delay_s", "first_attack_descriptor_lag_s",
            "first_majority_descriptor_lag_s"]
    a = a[cols].copy()
    for c in cols[4:]:
        a[c] = a[c].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
    return a


def delay_summary(data: Path, prefix: str = "M4_DELAY_") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per run and per configured delay: observed delays of vehicle_02 relative to vehicle_01."""
    rows = []
    for d in sorted((data / "analysis").glob(f"{prefix}*")):
        if not storage.exists(d / "temporal_alignment"):
            continue
        a = storage.read_table(d / "temporal_alignment", as_str=False)
        p = d / "temporal_alignment"
        ref = a[a.configured_delay_s == 0].iloc[0]
        v2 = a[a.vehicle_id != ref.vehicle_id]
        v2 = v2.iloc[0] if len(v2) else a[a.vehicle_id == "vehicle_02"].iloc[0]
        rep = int(p.parent.name.split("_R")[1].split("_")[0])
        rows.append({"configured_delay_s": v2.configured_delay_s, "rep": rep,
                     "first_malicious_frame_delay_s": v2.first_malicious_frame_delay_s,
                     "first_attack_descriptor_delay_s": v2.first_attack_descriptor_delay_s,
                     "first_majority_descriptor_delay_s": v2.first_majority_descriptor_delay_s,
                     "lag_v1_attack_desc_s": a[a.vehicle_id == "vehicle_01"].first_attack_descriptor_lag_s.iloc[0],
                     "lag_v2_attack_desc_s": a[a.vehicle_id == "vehicle_02"].first_attack_descriptor_lag_s.iloc[0],
                     "lag_v1_majority_desc_s": a[a.vehicle_id == "vehicle_01"].first_majority_descriptor_lag_s.iloc[0],
                     "lag_v2_majority_desc_s": a[a.vehicle_id == "vehicle_02"].first_majority_descriptor_lag_s.iloc[0]})
    runs = pd.DataFrame(rows).sort_values(["configured_delay_s", "rep"])
    runs["attack_desc_error_s"] = runs.first_attack_descriptor_delay_s - runs.configured_delay_s
    runs["majority_desc_error_s"] = runs.first_majority_descriptor_delay_s - runs.configured_delay_s
    agg = runs.groupby("configured_delay_s").agg(
        reps=("rep", "count"),
        frame_delay_min=("first_malicious_frame_delay_s", "min"), frame_delay_max=("first_malicious_frame_delay_s", "max"),
        attack_desc_delay_min=("first_attack_descriptor_delay_s", "min"), attack_desc_delay_max=("first_attack_descriptor_delay_s", "max"),
        majority_desc_delay_min=("first_majority_descriptor_delay_s", "min"), majority_desc_delay_max=("first_majority_descriptor_delay_s", "max"),
        max_abs_attack_desc_error=("attack_desc_error_s", lambda x: x.abs().max()),
        max_abs_majority_desc_error=("majority_desc_error_s", lambda x: x.abs().max()),
        lag_attack_desc_max=("lag_v2_attack_desc_s", "max"), lag_majority_desc_max=("lag_v2_majority_desc_s", "max")).reset_index()
    return runs, agg


def window_timing(data: Path, prefix: str = "M3_S0") -> pd.DataFrame:
    """Observed time spanned by one window and one stride (benign and replay-dominated windows)."""
    df = load(data, "M3_S")
    rows = []
    for name, g in {"benign": df[df.window_class == "benign_only"],
                    "replay >=50%": df[(df.attack_types == "replay") & (df.malicious_frame_ratio >= 0.5)],
                    "flooding >=50%": df[(df.attack_types == "flooding") & (df.malicious_frame_ratio >= 0.5)]}.items():
        span = (g.window_end_ns - g.window_start_ns) / 1e9
        rows.append({"windows": name, "n": len(g), "window_span_s_median": round(span.median(), 4),
                     "stride_s_median (50 frames)": round(span.median() * 50 / 99, 4)})
    return pd.DataFrame(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"))
    ap.add_argument("--prefix", default="M3_S")
    ap.add_argument("--delay", action="store_true", help="temporal-resolution summary of M4_DELAY_* sessions")
    a = ap.parse_args(argv)
    data = Path(a.data)
    if a.delay:
        runs, agg = delay_summary(data)
        fmt = lambda d: d.apply(lambda c: c.map(lambda x: f"{x:.4f}" if isinstance(x, float) else x))
        print("## Delay experiment: per run (vehicle_02 relative to vehicle_01, seconds)\n")
        print(table(fmt(runs)), "\n")
        print("## Delay experiment: per configured delay\n")
        print(table(fmt(agg)), "\n")
        print("## Time covered by one window / stride\n")
        print(table(window_timing(data)), "\n")
        return
    df = load(data, a.prefix)
    print(f"## Behavioural sanity check ({a.prefix}*: {len(df)} descriptors; median [IQR])\n")
    print(table(sanity(df)), "\n")
    print(f"## Temporal alignment ({a.prefix}*)\n")
    print(table(alignment(data, a.prefix)), "\n")


if __name__ == "__main__":
    main()
