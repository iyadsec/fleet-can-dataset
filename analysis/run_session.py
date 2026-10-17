"""Run the analysis layer on generated sessions:
   1. descriptors.csv      (observable inputs only)
   2. descriptor_labels.csv (evaluation join, afterwards)
   3. temporal_alignment.csv (campaign sessions; evaluation)
   4. descriptor_validation.json
Outputs go to data/analysis/<session_id>/ (git-ignored like all generated data).

Usage: python -m analysis.run_session <session_id> [...] [--data data]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .descriptor_extractor import PROJECT_ROOT, build_descriptors
from .descriptor_schema import LABEL_COLUMNS, OBSERVATION_COLUMNS
from .descriptor_validator import finish_checks, window_checks
from .labels import build_labels_from
from .temporal_alignment import campaign_alignment
from src import session_groups, storage
from src.session_groups import GROUP_VEHICLES


def analyse(session_id: str, data: Path, out_root: Path | None = None, group_size: int = GROUP_VEHICLES) -> dict:
    """Descriptors, labels, alignment and checks, computed one vehicle group at a time (generator 1.3.0): memory is
    bounded by the frames of `group_size` vehicles plus the (50x smaller) descriptor tables."""
    out = (out_root or data / "analysis") / session_id
    out.mkdir(parents=True, exist_ok=True)
    raw, gt = data / "raw" / session_id, data / "ground_truth" / session_id
    fmt = storage.table_format(raw / "can_frames")                 # same format as the raw session
    scenario = storage.read_table(raw / "can_frames", columns=["scenario_id"], nrows=1)
    scenario = scenario.scenario_id.iloc[0] if len(scenario) else ""
    desc_parts, label_parts, check_parts, first_mal = [], [], [], {}
    for grp in session_groups.vehicle_groups(session_groups.distinct(raw / "can_frames", "vehicle_id"), group_size):
        obs = session_groups.read_filtered(raw / "can_frames", "vehicle_id", grp, columns=OBSERVATION_COLUMNS)[OBSERVATION_COLUMNS]
        for c in ("tx_end_ns", "capture_time_ns", "dlc"):
            obs[c] = obs[c].astype("int64")
        desc = build_descriptors(obs)                               # step 1: no ground truth available here
        lab = session_groups.read_filtered(gt / "frame_labels", "frame_uid", set(obs.frame_uid)).set_index("frame_uid")
        labels = build_labels_from(desc, obs, lab, scenario)        # step 2: evaluation join
        check_parts.append(window_checks(desc, obs))
        mal = lab[lab.attack_id != "None"]
        if len(mal):
            t = obs.set_index("frame_uid").loc[mal.index, "tx_end_ns"].astype("int64")
            for a, v in t.groupby(mal.attack_id.to_numpy()).min().items():
                first_mal[a] = min(first_mal.get(a, v), int(v))
        if len(desc):
            desc_parts.append(desc)
            label_parts.append(labels)
        del obs, lab
        session_groups.release_memory()
    desc = pd.concat(desc_parts, ignore_index=True) if desc_parts else build_descriptors(_empty_obs())
    labels = pd.concat(label_parts, ignore_index=True) if label_parts else pd.DataFrame(columns=LABEL_COLUMNS)
    storage.write_frame(out / "descriptors", desc, fmt)
    storage.write_frame(out / "descriptor_labels", labels, fmt)
    align = campaign_alignment(data, session_id, desc, labels, first_mal=first_mal)
    if len(align):
        storage.write_frame(out / "temporal_alignment", align, fmt)
    checks = finish_checks(desc, labels, check_parts)
    overall = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    (out / "descriptor_validation.json").write_text(json.dumps({"session_id": session_id, "overall": overall,
                                                                "checks": checks}, indent=2))
    return {"session_id": session_id, "descriptors": len(desc), "overall": overall, "checks": checks,
            "window_classes": labels.window_class.value_counts().to_dict()}


def _empty_obs() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="int64" if c in ("tx_end_ns", "capture_time_ns", "dlc") else object)
                         for c in OBSERVATION_COLUMNS})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"))
    ap.add_argument("--group-size", type=int, default=GROUP_VEHICLES)
    a = ap.parse_args(argv)
    bad = 0
    for sid in a.sessions:
        r = analyse(sid, Path(a.data), group_size=a.group_size)
        print(f"{sid}: {r['descriptors']} descriptors, {r['window_classes']}, {r['overall']}")
        for c in r["checks"]:
            if c["status"] != "PASS":
                print("   ", c)
        bad += r["overall"] != "PASS"
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
