"""Milestone 5: enumerate the proposed final experiment matrix and estimate frames, storage and runtime
from MEASURED rates (Milestone 5 benchmark sessions). Nothing is generated.

Usage: .venv/bin/python scripts/m5_matrix_estimate.py [--data data] [--reps 5]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import storage  # noqa: E402

WARMUP_S, TAIL_S = 10.0, 5.0
ATTACK_DURATION_S = {"flooding": 5.0, "fuzzy": 5.0, "malfunction": 5.0, "replay": 5.0}
EXTRA_FPS = {"flooding": 1600, "fuzzy": 1000, "malfunction": 100, "replay": None}   # replay ~ doubles own rate
BENIGN_FPS = (350 + 270 + 350) / 3                                                   # mean of profiles A/B/C
PATTERNS = {"simultaneous": 0.0, "small_stagger": 0.5, "medium_stagger": 2.0, "large_stagger": 10.0, "partial_overlap": 5.0}
FAMILIES = ["flooding", "fuzzy", "malfunction", "replay"]


def matrix() -> list[dict]:
    rows = []
    for n in (2, 5, 10):
        def add(scenario, family, size, pattern, note=""):
            rows.append({"fleet": n, "scenario": scenario, "attack": family, "campaign_size": size, "pattern": pattern, "note": note})
        add("S0", "-", 0, "-")
        for f in FAMILIES:
            add("S1", f, 1, "isolated")
            add("S2", f, {2: 2, 5: 3, 10: 5}[n], "independent_close", "separate campaigns, starts within 0.5 s")
            sizes = [s for s in (2, 5, 10) if s <= n]
            for s in sizes:
                pats = list(PATTERNS) if s == n else ["simultaneous", "small_stagger"]
                for p in pats:
                    add("S3", f, s, p)
            if n >= 5:
                add("S3-mixed", f, {5: 2, 10: 5}[n], "small_stagger", f"+ {1 if n == 5 else 2} independent same-family attack(s)")
        add("S3-probe", "replay", 2, "sub_resolution_0.1s", "stress test below descriptor resolution")
    return rows


def duration(r: dict) -> float:
    if r["scenario"] == "S0":
        return 40.0
    k, p = r["campaign_size"], r["pattern"]
    d = ATTACK_DURATION_S.get(r["attack"], 5.0)
    if p == "partial_overlap":
        return WARMUP_S + PATTERNS[p] * (k - 1) + 10.0 + TAIL_S       # 10 s attacks, 5 s steps -> 50 % overlap
    step = PATTERNS.get(p, 0.5 if r["scenario"] == "S2" else (0.1 if "probe" in p else 0.0))
    if r["scenario"] in ("S1",):
        step = 0.0
    return WARMUP_S + step * max(k - 1, 0) + d + TAIL_S


def frames(r: dict, dur: float) -> float:
    base = r["fleet"] * BENIGN_FPS * dur
    if r["scenario"] == "S0":
        return base
    k = r["campaign_size"] + (1 if r["scenario"] == "S3-mixed" and r["fleet"] == 5 else 2 if r["scenario"] == "S3-mixed" else 0)
    d = 10.0 if r["pattern"] == "partial_overlap" else 5.0
    extra = EXTRA_FPS[r["attack"]] if EXTRA_FPS[r["attack"]] is not None else BENIGN_FPS
    return base + k * extra * d


def measured(data: Path) -> dict:
    out = {}
    for sid in ["M5_BENCH_S0_3V_PQ_FAST", "M5_BENCH_REPLAY_3V_PQ_FAST", "M5_BENCH_REPLAY_5V_PQ_FAST",
                "M5_BENCH_REPLAY_10V_PQ_FAST", "M5_BENCH_FLOODING_3V_PQ_FAST"]:
        md = json.loads((data / "metadata" / sid / "metadata.json").read_text())
        n_fr = md["transport_stats"]["total"]["matched"]
        tot = sum(p.stat().st_size for k in ("raw", "ground_truth", "metadata", "validation", "analysis")
                  for p in (data / k / sid).rglob("*") if p.is_file())
        out[sid] = {"frames": n_fr, "bytes_per_frame_all": tot / n_fr, "vehicles": md["fleet"]["vehicle_count"],
                    "wall_s": md["simulation"]["execution"]["wall_seconds"], "sim_s": md["simulation"]["sim_end_ns"] / 1e9}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--reps", type=int, default=5)
    a = ap.parse_args()
    data = Path(a.data)
    m = measured(data)
    bpf = max(v["bytes_per_frame_all"] for v in m.values())                    # conservative: largest measured
    # generation wall time per simulated second, by fleet size (orchestrator only; validation adds ~10-20 %)
    gen_rate = {3: m["M5_BENCH_REPLAY_3V_PQ_FAST"]["wall_s"] / m["M5_BENCH_REPLAY_3V_PQ_FAST"]["sim_s"],
                5: m["M5_BENCH_REPLAY_5V_PQ_FAST"]["wall_s"] / m["M5_BENCH_REPLAY_5V_PQ_FAST"]["sim_s"],
                10: m["M5_BENCH_REPLAY_10V_PQ_FAST"]["wall_s"] / m["M5_BENCH_REPLAY_10V_PQ_FAST"]["sim_s"]}
    rows = matrix()
    df = pd.DataFrame(rows)
    df["duration_s"] = [duration(r) for r in rows]
    df["frames"] = [frames(r, d) for r, d in zip(rows, df.duration_s)]
    df["gen_wall_s"] = [d * gen_rate[{2: 3, 5: 5, 10: 10}[n]] * 1.25 for n, d in zip(df.fleet, df.duration_s)]   # +25 % validation/analysis
    print("## Measured basis\n")
    for k, v in m.items():
        print(f"- {k}: {v['frames']} frames, {v['bytes_per_frame_all']:.1f} B/frame (Parquet raw+GT+metadata+validation+descriptors), "
              f"generation {v['wall_s']:.1f} s wall for {v['sim_s']:.0f} s simulated")
    print(f"\nUsing {bpf:.1f} B/frame (largest measured) and generation wall/sim ratios {({k: round(v, 3) for k, v in gen_rate.items()})} x 1.25.\n")
    summ = df.groupby(["fleet", "scenario"]).agg(configs=("attack", "count"), sim_s=("duration_s", "sum"),
                                                 frames=("frames", "sum"), wall_s=("gen_wall_s", "sum")).reset_index()
    print("## Configurations per fleet size and scenario (one repetition)\n")
    print("| fleet | scenario | configs | simulated s | frames (M) |\n|---|---|---|---|---|")
    for r in summ.itertuples():
        print(f"| {r.fleet} | {r.scenario} | {r.configs} | {r.sim_s:.0f} | {r.frames / 1e6:.2f} |")
    n_cfg, reps = len(df), a.reps
    tot_frames, tot_wall = df.frames.sum() * reps, df.gen_wall_s.sum() * reps
    print(f"\n**Total: {n_cfg} configurations x {reps} repetitions = {n_cfg * reps} runs; "
          f"{df.duration_s.sum() * reps / 3600:.1f} h simulated; {tot_frames / 1e6:.0f} M frames; "
          f"storage ~{tot_frames * bpf / 1e9:.1f} GB Parquet (CSV would be ~{tot_frames * 465 / 1e9:.0f} GB); "
          f"fast-mode wall time ~{tot_wall / 3600:.1f} h sequential.**")
    df.to_csv(data / "benchmarks" / "m5_matrix_proposal.csv", index=False)


if __name__ == "__main__":
    main()
