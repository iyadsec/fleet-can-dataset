"""Milestone 8.1 diagnostic: RSS of the bounded validator / analysis after every vehicle group (read-only).

Usage: .venv/bin/python scripts/m81_trace_groups.py {validator|analysis} <session> [--no-release] [--group-size 5]
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import session_groups  # noqa: E402


def rss():
    return int(next(l for l in Path("/proc/self/status").read_text().splitlines() if l.startswith("VmRSS")).split()[1]) // 1024


def hwm():
    return int(next(l for l in Path("/proc/self/status").read_text().splitlines() if l.startswith("VmHWM")).split()[1]) // 1024


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["validator", "analysis"])
    ap.add_argument("session")
    ap.add_argument("--no-release", action="store_true")
    ap.add_argument("--group-size", type=int, default=5)
    a = ap.parse_args()
    trace = []
    real_release = session_groups.release_memory

    def rel():
        if not a.no_release:
            real_release()
        trace.append((round(time.time() - T0, 1), rss(), hwm()))
    session_groups.release_memory = rel
    T0 = time.time()
    if a.what == "validator":
        from src import validator
        r = validator.validate(a.session, ROOT / "data", group_size=a.group_size)
        print("overall", r.overall)
    else:
        from analysis.run_session import analyse
        with tempfile.TemporaryDirectory() as d:
            print("overall", analyse(a.session, ROOT / "data", out_root=Path(d), group_size=a.group_size)["overall"])
    print("groups", len(trace), "rss_after_group(MB):", [x[1] for x in trace][::2], "final_hwm", hwm())
