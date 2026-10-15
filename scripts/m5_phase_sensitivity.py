"""Milestone 5 phase-sensitivity test for seeded switch-on offsets (benign traffic, in memory).

Benign frame timing depends only on each profile's message schedule and the vehicle's switch-on
offset (not on SUMO speed), so the descriptor grid can be measured exactly with the real
generator + bus model + descriptor extractor, without SUMO or vCAN.

For each candidate switch-on range and seed: fleet A, A, B, C (same-profile and cross-profile
pairs), 4 s of benign traffic, descriptors (100 frames, stride 50, bus time). Metric per vehicle
pair (i, j): for every window end of i in [1 s, 3.5 s], the distance to the nearest window end of j
("grid misalignment"; 0 = synchronized grids; for independent phases and equal stride period p it
is ~Uniform(0, p/2)).

Usage: .venv/bin/python scripts/m5_phase_sensitivity.py [--seeds 200]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_extractor import build_descriptors  # noqa: E402
from src.bus_model import CanBusModel  # noqa: E402
from src.can_generator import VehicleCanGenerator  # noqa: E402
from src.config import PROJECT_ROOT, _load_profiles, build_fleet  # noqa: E402
from src.sumo_controller import VehicleState  # noqa: E402

PROFILES = _load_profiles(yaml.safe_load(open(PROJECT_ROOT / "config/profiles/profiles_abc.yaml"))["profiles"], PROJECT_ROOT)
FLEET = ["profile_A", "profile_A", "profile_B", "profile_C"]
RANGES = {"none (0 s)": None, "0-0.1 s": [0.0, 0.1], "0-0.5 s": [0.0, 0.5], "0-1.0 s": [0.0, 1.0]}
STEP = 10_000_000


def window_ends(seed: int, rng_s) -> dict[str, np.ndarray]:
    sec = {"vehicles": [{"id": f"v{i}", "profile": p} for i, p in enumerate(FLEET)]}
    if rng_s:
        sec["switch_on"] = {"range_s": rng_s, "resolution_s": 0.000001}
    fleet, _ = build_fleet(sec, PROFILES, seed)
    rows = []
    for a in fleet:
        gen = VehicleCanGenerator(a.vehicle_id, a.channel, PROFILES[a.profile], a.switch_on_ns)
        bus = CanBusModel(PROFILES[a.profile].bus, a.channel)
        for k in range(1, 401):
            for f in gen.frames_for_step(VehicleState(k * STEP, a.vehicle_id, 12.0, 0.0, 0, 0, 90.0, "l", "e", "r"), STEP):
                bus.submit(f, f.node)
            for tx in bus.advance_until((k + 1) * STEP)[0]:
                rows.append({"frame_uid": f"{a.vehicle_id}-{len(rows)}", "session_id": "P", "vehicle_id": a.vehicle_id,
                             "can_id": f"0x{tx.frame.can_id:03X}", "dlc": tx.frame.dlc, "payload": tx.frame.data.hex().upper(),
                             "tx_end_ns": tx.end_ns, "capture_time_ns": tx.end_ns})
    d = build_descriptors(pd.DataFrame(rows))
    return {v: g.descriptor_timestamp_ns.to_numpy() for v, g in d.groupby("vehicle_id")}


def misalignment(ti: np.ndarray, tj: np.ndarray) -> np.ndarray:
    ti = ti[(ti >= 1_000_000_000) & (ti <= 3_500_000_000)]
    idx = np.searchsorted(tj, ti)
    lo, hi = tj[np.clip(idx - 1, 0, len(tj) - 1)], tj[np.clip(idx, 0, len(tj) - 1)]
    return np.minimum(np.abs(ti - lo), np.abs(ti - hi)) / 1e6           # ms


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=200)
    a = ap.parse_args(argv)
    pairs = {"A-A (same profile)": ("v0", "v1"), "A-B": ("v0", "v2"), "A-C": ("v0", "v3"), "B-C": ("v2", "v3")}
    rows = []
    for rname, rng_s in RANGES.items():
        acc = {p: [] for p in pairs}
        for seed in range(a.seeds):
            ends = window_ends(seed, rng_s)
            for p, (i, j) in pairs.items():
                acc[p].append(misalignment(ends[i], ends[j]))
        for p, chunks in acc.items():
            m = np.concatenate(chunks)
            rows.append({"switch_on_range": rname, "pair": p, "n": len(m), "mean_ms": round(m.mean(), 2),
                         "p05_ms": round(np.percentile(m, 5), 2), "p50_ms": round(np.percentile(m, 50), 2),
                         "p95_ms": round(np.percentile(m, 95), 2), "share_<1ms": round(float((m < 1).mean()), 3)})
    df = pd.DataFrame(rows)
    print("| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns))
    for r in df.itertuples(index=False):
        print("| " + " | ".join(str(x) for x in r) + " |")
    p_a = 50 * 1000 / 350
    print(f"\nReference: profile A stride = 50 frames / 350 fps = {p_a:.1f} ms -> independent phase gives "
          f"misalignment ~ Uniform(0, {p_a / 2:.1f}) ms, mean {p_a / 4:.1f} ms, p95 {0.95 * p_a / 2:.1f} ms.")


if __name__ == "__main__":
    main()
