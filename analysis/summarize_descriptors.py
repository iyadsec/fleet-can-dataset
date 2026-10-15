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
        if not (d / "descriptors.csv").exists():
            continue
        desc = pd.read_csv(d / "descriptors.csv")
        lab = pd.read_csv(d / "descriptor_labels.csv", **LIT, dtype=str)
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
    parts = [pd.read_csv(p) for p in sorted((data / "analysis").glob(f"{prefix}*/temporal_alignment.csv"))]
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"))
    ap.add_argument("--prefix", default="M3_S")
    a = ap.parse_args(argv)
    data = Path(a.data)
    df = load(data, a.prefix)
    print(f"## Behavioural sanity check ({a.prefix}*: {len(df)} descriptors; median [IQR])\n")
    print(table(sanity(df)), "\n")
    print(f"## Temporal alignment ({a.prefix}*)\n")
    print(table(alignment(data, a.prefix)), "\n")


if __name__ == "__main__":
    main()
