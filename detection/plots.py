"""M7 plots, generated only from saved results (descriptor_scores.parquet, threshold_calibration.json, CSVs)
plus the frozen descriptor labels (read-only)."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from detection.evaluate import FAMILIES  # noqa: E402

COLORS = {"benign_only": "#4c4c4c", "flooding": "#d62728", "fuzzy": "#ff7f0e", "malfunction": "#2ca02c", "replay": "#1f77b4"}
BINS = np.linspace(0, 1, 51)


def _thresholds(ax, th: dict, p: str | None) -> None:
    ax.axvline(0.55, color="k", ls=":", lw=1, label="θ_weak 0.55 (fixed)")
    ax.axvline(0.80, color="k", ls="--", lw=1, label="θ_strong 0.80 (fixed)")
    if p is not None:
        ax.axvline(th["profiles"][p]["theta_det"], color="purple", ls="-", lw=1.2, label=f"θ_det {th['profiles'][p]['theta_det']:.3f}")


def _hist(ax, x, label, color):
    if len(x):
        ax.hist(x, bins=BINS, density=True, histtype="step", lw=1.5, label=f"{label} (n={len(x):,})", color=color)


def make_plots(out_dir: Path, T: pd.DataFrame) -> list[Path]:
    out_dir = Path(out_dir)
    pdir = out_dir / "plots"
    pdir.mkdir(exist_ok=True)
    th = json.loads((out_dir / "threshold_calibration.json").read_text())
    written = []

    # 1. per profile: benign vs each family (attack-containing)
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5), sharey=True)
    for ax, p in zip(axes, ("A", "B", "C")):
        g = T[T.vehicle_profile == p]
        _hist(ax, g[g.label_A == 0].anomaly_score, "benign_only", COLORS["benign_only"])
        for f in FAMILIES:
            _hist(ax, g[(g.attack == f) & (g.label_A == 1)].anomaly_score, f, COLORS[f])
        _thresholds(ax, th, p)
        ax.set_yscale("log"); ax.set_title(f"Profile {p} — test anomaly_score"); ax.set_xlabel("anomaly_score")
        ax.legend(fontsize=7, loc="upper left")
    axes[0].set_ylabel("density (log)")
    written.append(pdir / "score_distribution_by_profile_attack.png")
    fig.tight_layout(); fig.savefig(written[-1], dpi=150); plt.close(fig)

    # 2/3. benign vs attack-containing; benign vs majority-malicious (pooled, ECDF)
    for lab, name, fname in (("label_A", "attack-containing", "benign_vs_attack_containing.png"),
                             ("label_B", "majority-malicious", "benign_vs_majority_malicious.png")):
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
        b = T[T.label_A == 0].anomaly_score
        a = T[T[lab] == 1].anomaly_score
        _hist(axes[0], b, "benign_only", COLORS["benign_only"]); _hist(axes[0], a, name, "#d62728")
        _thresholds(axes[0], th, None); axes[0].set_yscale("log"); axes[0].set_xlabel("anomaly_score"); axes[0].legend(fontsize=8)
        axes[0].set_title(f"Test: benign_only vs {name} (pooled)")
        for x, lbl, c in ((b, "benign_only", COLORS["benign_only"]), (a, name, "#d62728")):
            xs = np.sort(x.to_numpy()); axes[1].plot(xs, np.arange(1, len(xs) + 1) / len(xs), label=lbl, color=c)
        for p, ls in zip("ABC", ("-", "--", ":")):
            axes[1].axvline(th["profiles"][p]["theta_det"], color="purple", ls=ls, lw=1, label=f"θ_det {p}")
        axes[1].axvline(0.55, color="k", ls=":", lw=1); axes[1].axvline(0.80, color="k", ls="--", lw=1)
        axes[1].set_title("ECDF"); axes[1].set_xlabel("anomaly_score"); axes[1].legend(fontsize=8)
        written.append(pdir / fname)
        fig.tight_layout(); fig.savefig(written[-1], dpi=150); plt.close(fig)

    # 4. evidence bands of attack-containing windows (fixed and calibrated)
    eb = pd.read_csv(out_dir / "evidence_band_summary.csv")
    for bset, fname in (("original fixed 0.55/0.80", "evidence_bands_fixed.png"), ("calibrated FPR<=10%/<=5%", "evidence_bands_calibrated.png")):
        d = eb[(eb.band_set == bset) & (eb.profile != "all") & (~eb.windows.str.contains("majority"))]
        d = d.assign(lbl=d.windows.str.replace(" (all test sessions)", "") + "/" + d.profile)
        fig, ax = plt.subplots(figsize=(12, 4.5))
        bottom = np.zeros(len(d))
        for band, c in (("low", "#9ecae1"), ("weak", "#fdae6b"), ("strong", "#e6550d")):
            ax.bar(d.lbl, d[f"pct_{band}"], bottom=bottom, color=c, label=band); bottom += d[f"pct_{band}"].to_numpy()
        ax.set_ylabel("% of windows"); ax.set_title(f"Local evidence bands (test) — {bset}"); ax.legend()
        plt.setp(ax.get_xticklabels(), rotation=60, ha="right", fontsize=8)
        written.append(pdir / fname)
        fig.tight_layout(); fig.savefig(written[-1], dpi=150); plt.close(fig)

    # 5. campaign size and temporal pattern (attack-containing windows of S3 attacked vehicles)
    s3 = T[(T.scenario.isin(["S3", "S3-probe"])) & (T.label_A == 1)]
    for col, order, fname in (("campaign_size", [2, 5, 10], "campaign_size_scores.png"),
                              ("pattern", ["simultaneous", "small", "medium", "large", "partial", "probe"], "temporal_pattern_scores.png")):
        src = s3[s3.scenario == "S3"] if col == "campaign_size" else s3
        fig, axes = plt.subplots(1, 4, figsize=(17, 4), sharey=True)
        for ax, f in zip(axes, FAMILIES):
            g = src[src.attack == f]
            data = [g[g[col] == v].anomaly_score.to_numpy() for v in order]
            keep = [i for i, d in enumerate(data) if len(d)]
            ax.boxplot([data[i] for i in keep], tick_labels=[str(order[i]) for i in keep], showfliers=False)
            ax.axhline(0.55, color="k", ls=":", lw=1); ax.axhline(0.80, color="k", ls="--", lw=1)
            ax.set_title(f); ax.set_xlabel(col)
        axes[0].set_ylabel("anomaly_score (attack-containing)")
        written.append(pdir / fname)
        fig.tight_layout(); fig.savefig(written[-1], dpi=150); plt.close(fig)
    return written
