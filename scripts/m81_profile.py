"""Milestone 8.1: memory-ownership profile of the generator 1.2.0 orchestrator (diagnostic only).

Runs one experiment in-process under tracemalloc and records, at fixed points of the run, the RSS and the
top allocation sites (file:line) still alive. Writes results/m8_1_memory_scalability/profile_<session>.json.

Usage: .venv/bin/python scripts/m81_profile.py <experiment.yaml> <session_id>
"""

from __future__ import annotations

import json
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import orchestrator, recorder, storage, validator  # noqa: E402
from src.config import load_experiment  # noqa: E402

OUT = ROOT / "results" / "m8_1_memory_scalability"
marks = []


def rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS"):
            return int(line.split()[1]) / 1024
    return 0.0


def mark(label: str, top: int = 12) -> None:
    snap = tracemalloc.take_snapshot().filter_traces([tracemalloc.Filter(False, tracemalloc.__file__)])
    stats = snap.statistics("lineno")
    cur, peak = tracemalloc.get_traced_memory()
    marks.append({"label": label, "t": round(time.time() - T0, 1), "rss_mb": round(rss_mb(), 1),
                  "traced_mb": round(cur / 2**20, 1), "traced_peak_mb": round(peak / 2**20, 1),
                  "top": [{"site": f"{Path(s.traceback[0].filename).name}:{s.traceback[0].lineno}",
                           "mb": round(s.size / 2**20, 1), "blocks": s.count} for s in stats[:top]]})
    print(f"[{label}] rss={marks[-1]['rss_mb']} traced={marks[-1]['traced_mb']} peak={marks[-1]['traced_peak_mb']}", flush=True)


def wrap(mod, name, before=None, after=None):
    fn = getattr(mod, name)

    def w(*a, **k):
        if before:
            mark(before)
        out = fn(*a, **k)
        if after:
            mark(after)
        return out
    setattr(mod, name, w)


if __name__ == "__main__":
    cfg_path, sid = sys.argv[1], sys.argv[2]
    tracemalloc.start(1)
    T0 = time.time()
    wrap(recorder, "associate", before="end of simulation loop (all frames retained)", after="after associate (pairs)")
    wrap(recorder, "build_frame_rows", after="after build_frame_rows (row dicts)")
    orig_write = storage.write_table

    def write_table(base, columns, rows, fmt):
        if Path(base).name in ("can_frames", "frame_labels", "vehicle_states"):
            mark(f"before write {Path(base).name}")
        return orig_write(base, columns, rows, fmt)
    storage.write_table = write_table
    wrap(validator, "validate", before="before in-process validation", after="after in-process validation")
    cfg = load_experiment(cfg_path)
    orchestrator.run(cfg, session_id=sid)
    mark("end of run")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"profile_{sid}.json").write_text(json.dumps(marks, indent=1))
