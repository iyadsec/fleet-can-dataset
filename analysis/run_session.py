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

from .descriptor_extractor import PROJECT_ROOT, build_descriptors, load_observations
from .descriptor_validator import validate_session
from .labels import build_labels
from .temporal_alignment import campaign_alignment


def analyse(session_id: str, data: Path) -> dict:
    out = data / "analysis" / session_id
    out.mkdir(parents=True, exist_ok=True)
    obs = load_observations(data / "raw" / session_id)
    desc = build_descriptors(obs)                                   # step 1: no ground truth available here
    desc.to_csv(out / "descriptors.csv", index=False)
    labels = build_labels(desc, data, session_id)                   # step 2: evaluation join
    labels.to_csv(out / "descriptor_labels.csv", index=False)
    align = campaign_alignment(data, session_id, desc, labels)
    if len(align):
        align.to_csv(out / "temporal_alignment.csv", index=False)
    checks = validate_session(data, session_id, desc, labels)
    overall = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    (out / "descriptor_validation.json").write_text(json.dumps({"session_id": session_id, "overall": overall,
                                                                "checks": checks}, indent=2))
    return {"session_id": session_id, "descriptors": len(desc), "overall": overall, "checks": checks,
            "window_classes": labels.window_class.value_counts().to_dict()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--data", default=str(PROJECT_ROOT / "data"))
    a = ap.parse_args(argv)
    bad = 0
    for sid in a.sessions:
        r = analyse(sid, Path(a.data))
        print(f"{sid}: {r['descriptors']} descriptors, {r['window_classes']}, {r['overall']}")
        for c in r["checks"]:
            if c["status"] != "PASS":
                print("   ", c)
        bad += r["overall"] != "PASS"
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
