"""Session completion state for resumable generation (Milestone 9.1).

A session is COMPLETE only if every published directory exists, metadata.json (published last) parses, the
generator validation report says PASS, the analysis descriptor_validation.json (written last by
analysis/run_session.py) says PASS, and — when expected values are given — the provenance tags match
(e.g. matrix_id / matrix_sha256). Everything else is a well-defined resumable state:

  absent                      nothing exists                                   -> generate
  staged_incomplete           only data/.incomplete/<sid> (interrupted run)    -> generate (orchestrator clears the stage)
  partial_published           some session dirs but no metadata (crash while   -> clear_partial, then generate
                              publishing; never a complete session)
  generated_validation_failed metadata present, generator validation != PASS   -> STOP: human decision (never auto-overwrite)
  analysis_pending            generated + PASS, analysis missing/incomplete    -> run analysis only
  analysis_failed             descriptor validation != PASS                    -> STOP: human decision
  provenance_mismatch         complete, but tags differ from the expected      -> STOP: belongs to another matrix
  complete                    -> skip

Only clear_partial() deletes anything, and only for staged_incomplete / partial_published states (no metadata),
never for fleetcan-sim-1.1 (FC11_*) sessions.

Usage: .venv/bin/python scripts/session_state.py SID [SID ...] [--expect key=value ...] [--clear-partial]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PUBLISHED = ("raw", "ground_truth", "validation", "metadata")      # = src/orchestrator.SESSION_DIRS (metadata last)
PROTECTED_PREFIXES = ("FC11_",)


def _json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def session_state(sid: str, root: Path = DATA, expect: dict | None = None) -> dict:
    present = {d: (root / d / sid).exists() for d in PUBLISHED}
    stage = (root / ".incomplete" / sid).exists()
    meta = _json(root / "metadata" / sid / "metadata.json") if present["metadata"] else None
    out = {"session_id": sid, "present": present, "staged": stage}
    if not any(present.values()):
        out["state"] = "staged_incomplete" if stage else "absent"
        return out
    if meta is None:
        out["state"] = "partial_published"
        return out
    if not all(present.values()):
        out["state"] = "partial_published"            # metadata without its siblings cannot be produced by the orchestrator
        return out
    val = _json(root / "validation" / sid / "validation_report.json") or {}
    out["generator_validation"] = val.get("overall")
    if val.get("overall") != "PASS":
        out["state"] = "generated_validation_failed"
        return out
    dv = _json(root / "analysis" / sid / "descriptor_validation.json")
    out["descriptor_validation"] = dv.get("overall") if dv else None
    if dv is None:
        out["state"] = "analysis_pending"
        return out
    if dv.get("overall") != "PASS":
        out["state"] = "analysis_failed"
        return out
    tags = meta.get("tags") or {}
    mism = {k: (tags.get(k), v) for k, v in (expect or {}).items() if tags.get(k) != v}
    out["provenance_mismatch"] = mism
    out["state"] = "provenance_mismatch" if mism else "complete"
    return out


def clear_partial(sid: str, root: Path = DATA) -> list[str]:
    st = session_state(sid, root)["state"]
    if sid.startswith(PROTECTED_PREFIXES):
        raise SystemExit(f"{sid}: protected baseline session; refusing")
    if st not in ("staged_incomplete", "partial_published"):
        raise SystemExit(f"{sid}: state {st}; only staged_incomplete / partial_published may be cleared")
    if (root / "metadata" / sid / "metadata.json").exists():
        raise SystemExit(f"{sid}: metadata present; refusing")
    removed = []
    for p in [root / ".incomplete" / sid] + [root / d / sid for d in PUBLISHED + ("analysis", "generated_sumo")]:
        if p.exists():
            shutil.rmtree(p)
            removed.append(str(p.relative_to(root)))
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="+")
    ap.add_argument("--expect", action="append", default=[], help="key=value provenance tag expected in metadata.tags")
    ap.add_argument("--clear-partial", action="store_true")
    ap.add_argument("--data", default=str(DATA))
    a = ap.parse_args(argv)
    expect = dict(x.split("=", 1) for x in a.expect)
    for sid in a.sessions:
        if a.clear_partial:
            print(json.dumps({"session_id": sid, "removed": clear_partial(sid, Path(a.data))}))
        else:
            print(json.dumps(session_state(sid, Path(a.data), expect)))


if __name__ == "__main__":
    main()
