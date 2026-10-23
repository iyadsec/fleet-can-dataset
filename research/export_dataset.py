"""Export all 835 sessions of fleetcan-sim-scale-1.0 into fleetcan-sim-research-1.0 (resumable, deterministic).

Usage: .venv/bin/python -m research.export_dataset [--workers 3] [--sessions SID ...] [--force]
A session whose WORK/sessions/<sid>/DONE marker exists is skipped unless --force. Outputs of a session are a pure
function of its frozen source files, so re-running a session reproduces byte-identical Parquet/CSV content.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research.common import WORK, load_manifest          # noqa: E402


def _run(rec):
    from research.session_export import process
    try:
        s = process(rec)
        return rec["session_id"], "ok", s["seconds"], s["frames"]
    except Exception:                                    # recorded, never swallowed: the run reports failures
        return rec["session_id"], "error", traceback.format_exc(), 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--sessions", nargs="*")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    recs = load_manifest()
    if a.sessions:
        recs = [r for r in recs if r["session_id"] in set(a.sessions)]
    todo = [r for r in recs if a.force or not (WORK / "sessions" / r["session_id"] / "DONE").exists()]
    todo.sort(key=lambda r: -r["N"])                     # large sessions first for better packing
    print(f"{len(recs)} selected, {len(todo)} to run", flush=True)
    log = WORK / "export_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    errors = 0
    with Pool(a.workers, maxtasksperchild=20) as pool, open(log, "a") as lf:
        for i, (sid, status, info, frames) in enumerate(pool.imap_unordered(_run, todo), 1):
            lf.write(json.dumps({"session_id": sid, "status": status, "info": info, "frames": frames,
                                 "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
            lf.flush()
            errors += status != "ok"
            print(f"[{i}/{len(todo)}] {sid} {status} {info if status == 'ok' else ''} elapsed={time.time() - t0:.0f}s",
                  flush=True)
            if status != "ok":
                print(info, flush=True)
    print(f"done errors={errors}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
