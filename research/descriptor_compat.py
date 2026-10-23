"""Descriptor compatibility: release descriptors vs the reference FLEET-GUARD implementation (analysis/).

For each selected session the reference analysis.descriptor_extractor.build_descriptors (per-window Python loop,
reproduction of iyadsec/fleet-can-ids @ 4f1ebf3) is run on the SOURCE observations and compared with the release
descriptors (vectorised research/descriptors.py): window count, window boundaries (first/last frame), descriptor
timestamps and every one of the 24 features. The reference analysis.labels.build_labels_from is compared with the
release window labels (malicious_frame_count, window_class, attack_ids, campaign_ids, first malicious time).

Usage: .venv/bin/python -m research.descriptor_compat --out CSV [--sessions ...] [--per-config]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_extractor import build_descriptors, load_observations   # noqa: E402
from analysis.descriptor_schema import FEATURE_COLUMNS                             # noqa: E402
from analysis.labels import build_labels_from                                      # noqa: E402
from research.common import RELEASE, SRC, atomic_csv, load_manifest                # noqa: E402
from src import storage                                                            # noqa: E402


def compare(sid: str) -> dict:
    obs = load_observations(SRC / "raw" / sid)
    ref = build_descriptors(obs, "transmission", 0)
    rel = pd.read_parquet(RELEASE / "data" / "descriptors" / sid / "descriptors.parquet")
    rel_lab = pd.read_parquet(RELEASE / "data" / "ground_truth" / sid / "window_labels.parquet")
    ref = ref.sort_values(["vehicle_id", "window_index"]).reset_index(drop=True)
    rel = rel.sort_values(["vehicle_id", "window_index"]).reset_index(drop=True)
    out = {"session_id": sid, "reference_windows": len(ref), "release_windows": len(rel)}
    out["window_count_equal"] = len(ref) == len(rel)
    if not out["window_count_equal"]:
        return out
    uid_idx = lambda s: s.str.rsplit("-F", n=1).str[1].astype("int64").to_numpy()     # noqa: E731
    out["window_keys_equal"] = bool((ref.vehicle_id.values == rel.vehicle_id.values).all()
                                    and (ref.window_index.values == rel.window_index.values).all())
    out["first_frame_equal"] = bool((uid_idx(ref.first_frame_uid) == rel.first_frame_index.values).all())
    out["last_frame_equal"] = bool((uid_idx(ref.last_frame_uid) == rel.last_frame_index.values).all())
    out["descriptor_timestamp_equal"] = bool((ref.descriptor_timestamp_ns.astype("int64").values == rel.descriptor_timestamp_ns.values).all())
    out["window_start_equal"] = bool((ref.window_start_ns.astype("int64").values == rel.window_start_ns.values).all())
    out["nan_filled_equal"] = bool((ref.nan_filled_features.astype("int64").values == rel.nan_filled_features.values).all())
    worst, worst_f, n_exact = 0.0, "", 0
    for f in FEATURE_COLUMNS:
        a = ref[f].astype(float).to_numpy()
        b = rel[f].to_numpy()
        d = np.abs(a - b)
        tol = 1e-9 * np.maximum(1.0, np.abs(a))
        out[f"max_abs_diff_{f}"] = float(d.max()) if d.size else 0.0
        out[f"within_tol_{f}"] = bool((d <= tol).all())
        n_exact += int((a == b).sum())
        if d.size and d.max() > worst:
            worst, worst_f = float(d.max()), f
    out["feature_values"] = len(ref) * 24
    out["feature_values_bit_identical"] = n_exact
    out["max_abs_feature_diff"] = worst
    out["max_abs_feature_diff_feature"] = worst_f
    out["all_features_within_1e-9_relative"] = all(out[f"within_tol_{f}"] for f in FEATURE_COLUMNS)
    # labels (reference rule: analysis/labels.py; computed from ground truth only after descriptors exist)
    lab = storage.read_table(SRC / "ground_truth" / sid / "frame_labels").set_index("frame_uid")
    scen = storage.read_table(SRC / "raw" / sid / "can_frames", columns=["scenario_id"], nrows=1).scenario_id.iloc[0]
    rlab = build_labels_from(ref, obs, lab, scen).sort_values(["vehicle_id", "window_index"]).reset_index(drop=True)
    rl = rel_lab.sort_values(["vehicle_id", "window_index"]).reset_index(drop=True)
    out["label_count_equal"] = bool((rlab.malicious_frame_count.astype(int).values == rl.malicious_frame_count.values).all())
    out["label_class_equal"] = bool((rlab.window_class.values == rl.window_class.values).all())
    out["label_attack_ids_equal"] = bool((rlab.attack_ids.values == rl.attack_ids.values).all())
    out["label_campaign_ids_equal"] = bool((rlab.campaign_ids.values == rl.campaign_ids.values).all())
    fm_ref = rlab.first_malicious_time_ns.replace("", "-1").astype("int64").values
    out["label_first_malicious_time_equal"] = bool((fm_ref == rl.first_malicious_timestamp_ns.values).all())
    out["majority_label_rule_equal"] = bool(((rlab.malicious_frame_ratio.astype(float).values >= 0.5).astype(int)
                                             == rl.label_majority_original.values).all())
    out["compatible"] = bool(out["window_keys_equal"] and out["first_frame_equal"] and out["last_frame_equal"]
                             and out["descriptor_timestamp_equal"] and out["nan_filled_equal"]
                             and out["all_features_within_1e-9_relative"] and out["label_count_equal"]
                             and out["label_class_equal"] and out["label_attack_ids_equal"]
                             and out["label_campaign_ids_equal"] and out["label_first_malicious_time_equal"]
                             and out["majority_label_rule_equal"])
    return out


def per_config_selection() -> list[str]:
    """One session per (scenario, attack, N, phase): every family, scenario, fleet size and route group; repetition
    chosen deterministically as R((config_index mod 5) + 1) of the first configuration of the cell."""
    recs = load_manifest()
    cells = {}
    for r in sorted(recs, key=lambda r: (r["config_index"], r["repetition"])):
        key = (r["scenario"], r["attack"], r["N"], r["phase"])
        if key not in cells and r["repetition"] == (r["config_index"] % 5) + 1:
            cells[key] = r["session_id"]
    return sorted(cells.values())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--sessions", nargs="*")
    ap.add_argument("--per-config", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    sids = a.sessions or []
    if a.per_config:
        sids = sorted(set(sids) | set(per_config_selection()))
    from multiprocessing import Pool
    with Pool(a.workers) as p:
        rows = p.map(compare, sids)
    df = pd.DataFrame(rows)
    atomic_csv(pa.Table.from_pandas(df, preserve_index=False), Path(a.out))
    print(df[["session_id", "reference_windows", "compatible", "max_abs_feature_diff", "max_abs_feature_diff_feature"]].to_string())
    print("all compatible:", bool(df.compatible.all()))


if __name__ == "__main__":
    main()
