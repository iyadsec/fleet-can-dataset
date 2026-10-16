"""Milestone 5.1 jitter sensitivity (benign only; no detector, no labels in any parameter).

Sessions: M51_JIT_J{0,1,2,5}_R{1..5} (3 vehicles A/B/C, switch-on 0-1.0 s, fast mode), analysed with
analysis.run_session. Selection criterion fixed BEFORE looking at results (see the Milestone 5.1 report):
smallest jitter for which (1) no informative feature has zero within-vehicle variance, (2) per-vehicle
normalised benign descriptors have p99 |z| in single digits on held-out benign windows, (3) periodic structure
is preserved (release deviation <= f x period, no drift; enforced by validator V10/V12).

Usage: .venv/bin/python scripts/m51_jitter_sensitivity.py [--data data]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_schema import FEATURE_COLUMNS  # noqa: E402
from src import storage  # noqa: E402

CAL_END_NS = 8_000_000_000
KEY = ["mean_inter_arrival_time", "std_inter_arrival_time", "can_id_entropy", "most_common_can_id_ratio",
       "mean_dlc", "std_dlc", "byte_mean_0", "byte_std_0"]
TIMING_ID = ["mean_inter_arrival_time", "std_inter_arrival_time", "can_id_entropy", "most_common_can_id_ratio"]


def table(df):
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)])


def sessions(data: Path):
    for d in sorted((data / "analysis").glob("M51_JIT_J*_R*")):
        m = re.match(r"M51_JIT_J(\d+)_R(\d+)", d.name)
        yield int(m.group(1)), int(m.group(2)), d.name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    data = Path(ap.parse_args().data)
    var_rows, z_rows, per_rows = [], [], []
    for j, r, sid in sessions(data):
        meta = json.loads((data / "metadata" / sid / "metadata.json").read_text())
        prof = {v["vehicle_id"]: v["profile"][-1] for v in meta["fleet"]["vehicles"]}
        d = storage.read_table(data / "analysis" / sid / "descriptors", as_str=False)
        raw = storage.read_table(data / "raw" / sid / "can_frames", columns=["vehicle_id", "can_id", "sim_time_ns"])
        raw["sim_time_ns"] = raw.sim_time_ns.astype("int64")
        for v, g in d.groupby("vehicle_id"):
            informative = [c for c in FEATURE_COLUMNS if c != "frame_count"]
            zero_var = [c for c in informative if g[c].std(ddof=0) == 0]
            var_rows.append({"jitter_pct": j, "rep": r, "vehicle": v, "profile": prof[v], "zero_var_features": len(zero_var),
                             "zero_var_list": ",".join(zero_var), **{f"sd_{c}": g[c].std(ddof=0) for c in KEY},
                             **{f"mean_{c}": g[c].mean() for c in KEY}})
            cal, test = g[g.descriptor_timestamp_ns < CAL_END_NS], g[g.descriptor_timestamp_ns >= CAL_END_NS]
            mu, sd = cal[FEATURE_COLUMNS].mean(), cal[FEATURE_COLUMNS].std(ddof=0)
            for c in TIMING_ID:
                if sd[c] == 0:
                    z_rows.append({"jitter_pct": j, "feature": c, "z": np.inf if (test[c] != mu[c]).any() else 0.0})
                else:
                    z_rows += [{"jitter_pct": j, "feature": c, "z": abs(x)} for x in (test[c] - mu[c]) / sd[c]]
            # observed per-stream interval spread (generation times), relative to the stream's median interval
            rv = raw[raw.vehicle_id == v]
            for cid, s in rv.groupby("can_id"):
                iv = np.diff(np.sort(s.sim_time_ns.to_numpy()))
                if len(iv) > 10:
                    P = np.median(iv)
                    per_rows.append({"jitter_pct": j, "profile": prof[v], "period_ms": P / 1e6,
                                     "interval_cv_pct": 100 * iv.std() / P, "max_dev_pct": 100 * np.abs(iv - P).max() / P})
    V, Z, PR = pd.DataFrame(var_rows), pd.DataFrame(z_rows), pd.DataFrame(per_rows)

    out = []
    for j, g in V.groupby("jitter_pct"):
        row = {"jitter": f"+-{j}%", "vehicle-runs": len(g), "zero-variance features (median / max of 23)":
               f"{int(g.zero_var_features.median())} / {int(g.zero_var_features.max())}"}
        for c in ["mean_inter_arrival_time", "std_inter_arrival_time", "can_id_entropy", "most_common_can_id_ratio"]:
            cv = 100 * g[f"sd_{c}"] / g[f"mean_{c}"].abs()
            row[f"{c} within-vehicle CV % (median)"] = f"{cv.median():.3g}"
        out.append(row)
    print("## Benign within-vehicle variation (15 vehicle-runs per level: 5 seeds x A/B/C)\n")
    print(table(pd.DataFrame(out)), "\n")

    print("## Within-vehicle std by profile (median over seeds) for key features\n")
    prof_rows = []
    for (j, p), g in V.groupby(["jitter_pct", "profile"]):
        prof_rows.append({"jitter": f"+-{j}%", "profile": p, **{c: f"{g[f'sd_{c}'].median():.3g}" for c in KEY}})
    print(table(pd.DataFrame(prof_rows)), "\n")

    print("## Per-vehicle normalised benign descriptors (calibration < 8 s, held-out benign windows >= 8 s), |z|\n")
    zr = []
    for (j, c), g in Z.groupby(["jitter_pct", "feature"]):
        z = g.z.replace(np.inf, np.nan)
        zr.append({"jitter": f"+-{j}%", "feature": c, "undefined (zero calibration std)": int(np.isinf(g.z).sum()),
                   "p50": f"{z.median():.2f}", "p99": f"{z.quantile(0.99):.2f}", "max": f"{z.max():.2f}"})
    print(table(pd.DataFrame(zr)), "\n")

    print("## Periodic structure: observed generation-interval spread per message stream\n")
    pr = PR.groupby("jitter_pct").agg(streams=("period_ms", "count"), interval_cv_pct_median=("interval_cv_pct", "median"),
                                      interval_cv_pct_max=("interval_cv_pct", "max"), max_dev_pct=("max_dev_pct", "max")).reset_index()
    pr["jitter"] = pr.jitter_pct.map(lambda x: f"+-{x}%")
    print(table(pr[["jitter", "streams", "interval_cv_pct_median", "interval_cv_pct_max", "max_dev_pct"]].round(3)), "\n")
    print("Equivalent absolute release jitter per period: 10 ms -> +-0.1/0.2/0.5 ms, 20 ms -> +-0.2/0.4/1.0 ms, "
          "50 ms -> +-0.5/1.0/2.5 ms for 1/2/5 %. Interval between consecutive frames can deviate by up to 2f x period.")


if __name__ == "__main__":
    main()
