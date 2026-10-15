"""Milestone 5 benchmark report from scripts/m5_benchmark.sh outputs (measured on the VM).

1. Equivalence: CSV_FAST vs PQ_FAST (storage) and PQ_RT vs PQ_FAST (execution mode), all logical tables
   + descriptors (scripts/compare_sessions.py).
2. Storage: frames, CSV vs Parquet bytes (raw + ground truth), compression ratio, table read time,
   table write time (re-writing the session's tables through src/storage.py), descriptor-input read time.
3. Execution: simulated vs wall-clock duration, speedup, CPU %, max RSS (from /usr/bin/time -v).

Usage: .venv/bin/python scripts/m5_bench_report.py [--data data]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_extractor import load_observations  # noqa: E402
from compare_sessions import compare  # noqa: E402
from src import storage  # noqa: E402

TABLE_DIRS = [("raw", ["can_frames", "vehicle_states", "unmatched_captures"]),
              ("ground_truth", ["frame_labels", "dropped_frames", "attacks", "attack_frames", "replay_map",
                                "campaigns", "campaign_membership", "campaign_overlap"])]


def bytes_of(data: Path, sid: str) -> int:
    return sum(storage.table_file(data / d / sid / t).stat().st_size for d, ts in TABLE_DIRS for t in ts
               if storage.exists(data / d / sid / t))


def timed_read(data: Path, sid: str) -> float:
    t = time.perf_counter()
    for d, ts in TABLE_DIRS:
        for name in ts:
            if storage.exists(data / d / sid / name):
                storage.read_table(data / d / sid / name)
    return time.perf_counter() - t


def timed_write(data: Path, sid: str, fmt: str) -> float:
    tables = [(t, storage.read_table(data / d / sid / t)) for d, ts in TABLE_DIRS for t in ts if storage.exists(data / d / sid / t)]
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.perf_counter()
        for name, df in tables:
            rows = df.to_dict("records")
            if fmt == "parquet":                         # restore the generator's int columns
                for c in df.columns:
                    if df[c].str.fullmatch(r"-?\d+").all() and len(df):
                        for r in rows:
                            r[c] = int(r[c])
            storage.write_table(Path(tmp) / name, list(df.columns), rows, fmt)
        return time.perf_counter() - t0


def timed_obs(data: Path, sid: str) -> float:
    t = time.perf_counter()
    load_observations(data / "raw" / sid)
    return time.perf_counter() - t


def time_v(path: Path) -> dict:
    txt = path.read_text() if path.exists() else ""
    def grab(pat):
        m = re.search(pat, txt)
        return m.group(1) if m else None
    wall = grab(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([\d:.]+)")
    secs = None
    if wall:
        parts = [float(x) for x in wall.split(":")]
        secs = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    return {"wall_s": secs, "cpu_pct": grab(r"Percent of CPU this job got: (\d+)%"),
            "max_rss_mb": round(int(grab(r"Maximum resident set size \(kbytes\): (\d+)")) / 1024, 1) if grab(r"Maximum resident set size \(kbytes\): (\d+)") else None}


def table(df: pd.DataFrame) -> str:
    return "\n".join(["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    data = Path(ap.parse_args().data)
    names = sorted({re.sub(r"_(CSV_FAST|PQ_FAST|PQ_RT)$", "", p.name) for p in (data / "raw").glob("M5_BENCH_*")})
    eq, st, ex = [], [], []
    for n in names:
        csv_s, pq_s, rt_s = f"{n}_CSV_FAST", f"{n}_PQ_FAST", f"{n}_PQ_RT"
        r = compare(data, csv_s, pq_s)
        eq.append({"experiment": n.replace("M5_BENCH_", ""), "comparison": "CSV vs Parquet (fast)",
                   "tables_identical": sum(v.startswith("IDENTICAL") for v in r.values()), "tables": len(r),
                   "descriptors": r.get("descriptors", "-")[:60]})
        if (data / "raw" / rt_s).exists():
            r2 = compare(data, rt_s, pq_s)
            eq.append({"experiment": n.replace("M5_BENCH_", ""), "comparison": "real-time vs fast (Parquet)",
                       "tables_identical": sum(v.startswith("IDENTICAL") for v in r2.values()), "tables": len(r2),
                       "descriptors": r2.get("descriptors", "-")[:60]})
        meta = json.loads((data / "metadata" / pq_s / "metadata.json").read_text())
        frames = meta["transport_stats"]["total"]["matched"]
        b_csv, b_pq = bytes_of(data, csv_s), bytes_of(data, pq_s)
        st.append({"experiment": n.replace("M5_BENCH_", ""), "vehicles": meta["fleet"]["vehicle_count"], "frames": frames,
                   "csv_MB": round(b_csv / 1e6, 2), "parquet_MB": round(b_pq / 1e6, 2), "ratio": round(b_csv / b_pq, 1),
                   "csv_B/frame": round(b_csv / frames), "parquet_B/frame": round(b_pq / frames, 1),
                   "read_csv_s": round(timed_read(data, csv_s), 2), "read_pq_s": round(timed_read(data, pq_s), 2),
                   "write_csv_s": round(timed_write(data, csv_s, "csv"), 2), "write_pq_s": round(timed_write(data, pq_s, "parquet"), 2),
                   "obs_read_csv_s": round(timed_obs(data, csv_s), 3), "obs_read_pq_s": round(timed_obs(data, pq_s), 3)})
        sim = meta["simulation"]["sim_end_ns"] / 1e9
        for sid, mode in [(pq_s, "fast"), (rt_s, "real-time")]:
            if not (data / "metadata" / sid).exists():
                continue
            tv = time_v(data / "benchmarks" / "m5" / f"{sid}.time")
            ex.append({"experiment": n.replace("M5_BENCH_", ""), "vehicles": meta["fleet"]["vehicle_count"], "mode": mode,
                       "simulated_s": round(sim, 1), "wall_s (incl. validation)": tv["wall_s"],
                       "speedup": round(sim / tv["wall_s"], 2) if tv["wall_s"] else None,
                       "cpu_%": tv["cpu_pct"], "max_rss_MB": tv["max_rss_mb"],
                       "frames_per_wall_s": round(frames / tv["wall_s"]) if tv["wall_s"] else None})
    print("## Equivalence\n\n" + table(pd.DataFrame(eq)) + "\n")
    print("## Storage (raw + ground truth tables)\n\n" + table(pd.DataFrame(st)) + "\n")
    print("## Execution\n\n" + table(pd.DataFrame(ex)) + "\n")


if __name__ == "__main__":
    main()
