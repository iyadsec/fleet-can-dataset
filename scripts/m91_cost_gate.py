"""Milestone 9.1: cost estimate of the FROZEN matrix (167 configurations / 835 sessions) and the disk-capacity gate.

Read-only: reads the frozen matrix, measured M8/M8.1 metrics and filesystem / block-device sizes; changes nothing.
Cost model = scripts/m9_design.py (calibrated on 22 measured N >= 20 sessions; frames -0.8..+1.9 %, storage -1.4..+6.7 %,
time -3.9..+10.1 %). Raw vs derived split and temporary space use measured per-frame coefficients (below).

Writes results/m9_1_freeze/cost_estimate.json and results/m9_1_freeze/disk_gate.json.
Usage: .venv/bin/python scripts/m91_cost_gate.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import m9_design as D  # noqa: E402
import m91_freeze as F  # noqa: E402

OUT = ROOT / "results" / "m9_1_freeze"
GB = 1e9
# measured (generator 1.3.0): raw/ (can_frames, vehicle_states, unmatched) per 1e6 frames: N100 S0 40 s 45.4 MB,
# N100 replay K=100 44.5 MB, N100 S0 120 s 43.9 MB -> 45 MB; everything else (GT, descriptors, labels, metadata,
# validation) is the remainder of the calibrated total
RAW_MB_PER_MFRAME = 45.0
# measured temporary-storage peak (staging + .part files) per 1e6 frames: 85-91 MB (N100 40-180 s) -> 92 MB
TEMP_MB_PER_MFRAME = 92.0
GENERATED_SUMO_MB_PER_SESSION = 0.15      # measured 0.115-0.146 MB (data/generated_sumo/<sid>)
UNCERTAINTY = 0.10                        # +10 % on the predicted dataset (calibration max underestimate 1.4 %;
                                          # flooding/fuzzy at K = 50/100 extrapolated in attack load)
TEMP_SAFETY = 2.0                         # x2 on the largest session's temporary peak, rounded up to >= 1 GB
OPERATIONAL_RESERVE_GB = 3.0              # logs, run log, hashing/freeze, analysis scratch, OS headroom


def model(c: dict) -> dict:
    return D.session_cost({**c, "mixed_independent_count": c["mixed_attack_count"]})


def estimate(rows):
    tot = defaultdict(float)
    by = {"N": defaultdict(lambda: defaultdict(float)), "scenario": defaultdict(lambda: defaultdict(float)),
          "phase": defaultdict(lambda: defaultdict(float))}
    largest = (0, None)
    for c in rows:
        s = model(c)
        r = c["repetitions"]
        part = {"configurations": 1, "sessions": r, "simulated_h": s["sim_s"] * r / 3600, "vehicle_h": s["vehicle_s"] * r / 3600,
                "frames_M": s["frames"] * r / 1e6, "descriptors_M": s["descriptors"] * r / 1e6,
                "raw_GB": RAW_MB_PER_MFRAME * s["frames"] / 1e6 * r / 1e3, "total_GB": s["mb"] * r / 1e3,
                "generation_h": s["gen_s"] * r / 3600, "analysis_h": s["analysis_s"] * r / 3600}
        part["derived_GB"] = part["total_GB"] - part["raw_GB"]
        part["wall_h"] = part["generation_h"] + part["analysis_h"]
        for k, v in part.items():
            tot[k] += v
            by["N"][c["N"]][k] += v
            by["scenario"][c["scenario"]][k] += v
            by["phase"][str(c["phase"])][k] += v
        if s["frames"] > largest[0]:
            largest = (s["frames"], c["config_id"])
    rnd = lambda d: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}
    return {"total": rnd(tot), "by_N": {k: rnd(v) for k, v in sorted(by["N"].items())},
            "by_scenario": {k: rnd(v) for k, v in sorted(by["scenario"].items())},
            "by_phase": {k: rnd(v) for k, v in sorted(by["phase"].items())},
            "largest_session": {"config_id": largest[1], "frames": round(largest[0]), "temp_peak_GB": round(TEMP_MB_PER_MFRAME * largest[0] / 1e6 / 1e3, 3)}}


def disk():
    st = os.statvfs("/")
    size, free_user = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    reserved = (st.f_bfree - st.f_bavail) * st.f_frsize
    lb = subprocess.run(["lsblk", "-b", "-J", "-o", "NAME,SIZE,TYPE"], capture_output=True, text=True, check=True)
    devs = json.loads(lb.stdout)["blockdevices"]
    pv = lv = None
    for d in devs:
        for p in d.get("children") or []:
            for c in p.get("children") or []:
                if c["type"] == "lvm":
                    pv, lv = int(p["size"]), int(c["size"])
    return {"filesystem_size_GB": size / GB, "filesystem_used_GB": used / GB, "filesystem_free_GB": free_user / GB,
            "root_reserved_GB": reserved / GB, "reserved_fraction": reserved / size, "pv_partition_GB": pv / GB, "lv_GB": lv / GB,
            "vg_unallocated_GB_approx": (pv - lv) / GB}


def main():
    doc = yaml.load(F.MATRIX.read_bytes(), Loader=yaml.CSafeLoader)
    rows = doc["configurations"]
    est = estimate(rows)
    est.update({"matrix_id": doc["matrix_id"], "configurations": len(rows), "sessions": sum(r["repetitions"] for r in rows),
                "cost_model": D.COST, "raw_MB_per_Mframe": RAW_MB_PER_MFRAME,
                "range": "interpolation within measured N 10-100, 20-180 s, K <= 100 (replay); extrapolated: attack load of "
                         "flooding/fuzzy campaigns with K = 50/100 (per-frame cost assumed constant); G0 at N = 10 checked against "
                         "M8_S0_N010 (frames -0.5 %, time +9 %)",
                "execution": "sequential, one session at a time, generation then analysis (as measured)"})
    (OUT / "cost_estimate.json").write_text(json.dumps(est, indent=1) + "\n")
    dk = disk()
    dataset = est["total"]["total_GB"]
    gsumo = GENERATED_SUMO_MB_PER_SESSION * est["sessions"] / 1e3
    temp = max(1.0, TEMP_SAFETY * est["largest_session"]["temp_peak_GB"])
    required = dataset * (1 + UNCERTAINTY) + gsumo + temp + OPERATIONAL_RESERVE_GB
    after_ext = dk["filesystem_free_GB"] + dk["vg_unallocated_GB_approx"] * (1 - dk["reserved_fraction"])
    gate = {"filesystem": {k: round(v, 3) for k, v in dk.items()},
            "requirement": {"predicted_dataset_GB": round(dataset, 2), "uncertainty_margin": UNCERTAINTY,
                            "dataset_with_margin_GB": round(dataset * (1 + UNCERTAINTY), 2), "generated_sumo_GB": round(gsumo, 2),
                            "temporary_GB": round(temp, 2), "operational_reserve_GB": OPERATIONAL_RESERVE_GB,
                            "minimum_free_before_M10_GB": round(required, 2)},
            "current_free_GB": round(dk["filesystem_free_GB"], 2),
            "shortfall_GB": round(max(0.0, required - dk["filesystem_free_GB"]), 2),
            "free_after_full_vg_extension_GB_approx": round(after_ext, 2),
            "vg_extension_alone_sufficient": after_ext >= required,
            "margin_after_vg_extension_GB": round(after_ext - required, 2),
            "DISK_CAPACITY_GATE": "PASS" if dk["filesystem_free_GB"] >= required else "FAIL",
            "note": "not changed by M9.1; LV extension (sudo) is the user's action; coverage is not reduced to fit the disk"}
    (OUT / "disk_gate.json").write_text(json.dumps(gate, indent=1) + "\n")
    print(json.dumps({"total": est["total"], "by_phase": est["by_phase"]}, indent=1))
    print(json.dumps(gate, indent=1))


if __name__ == "__main__":
    main()
