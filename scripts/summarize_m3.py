"""Summarise the latest Milestone 3 sessions from their saved files (no hand-computed numbers).

Usage: .venv/bin/python scripts/summarize_m3.py [--data data] > m3_summary.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pandas as pd

LIT = dict(keep_default_na=False, na_filter=False, dtype=str)
MATRIX = ["s0_benign", "s1_isolated_replay", "s2_independent_replay", "s3_simultaneous_replay",
          "s3_small_stagger_replay", "s3_large_stagger_replay", "s3_partial_overlap_replay", "s3_random_delay_replay",
          "s3_coordinated_flooding", "s3_coordinated_fuzzy", "s3_coordinated_malfunction", "s3_mixed_replay"]
SCALE = ["scale_2", "scale_5", "scale_10"]


def latest(data: Path, exp_id: str):
    s = sorted(p.name for p in (data / "raw").glob(f"{exp_id}_2*"))
    return s[-1] if s else None


def table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] +
                     ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)])


def du(path: Path) -> str:
    return subprocess.run(["du", "-sh", str(path)], capture_output=True, text=True).stdout.split()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    data = Path(ap.parse_args().data)
    rows, sessions = [], {}
    for name in MATRIX + SCALE:
        sid = latest(data, f"M3_{name.upper()}" if not name.startswith("scale") else f"M3_SCALE_{name.split('_')[1]}V")
        if not sid:
            continue
        sessions[name] = sid
        v = json.loads((data / "validation" / sid / "validation_report.json").read_text())
        meta = json.loads((data / "metadata" / sid / "metadata.json").read_text())
        st = {}
        for c in v["checks"]:
            st[c["status"]] = st.get(c["status"], 0) + 1
        f = pd.read_csv(data / "raw" / sid / "can_frames.csv", usecols=["frame_uid"], **LIT)
        lab = pd.read_csv(data / "ground_truth" / sid / "frame_labels.csv", usecols=["frame_label"], **LIT)
        drops = pd.read_csv(data / "ground_truth" / sid / "dropped_frames.csv", usecols=["drop_kind"], **LIT)
        camp = pd.read_csv(data / "ground_truth" / sid / "campaigns.csv", **LIT)
        size = sum(du_b for du_b in [sum(p.stat().st_size for p in (data / k / sid).rglob("*") if p.is_file())
                                     for k in ("raw", "ground_truth", "metadata", "validation")])
        rows.append({"run": name, "scenario": meta["scenario_id"], "vehicles": meta["fleet"]["vehicle_count"],
                     "campaigns": ";".join(f"{r.campaign_id}({'coord' if r.coordinated == 'true' else 'indep'},{r.delay_pattern})"
                                           for r in camp.itertuples()) or "-",
                     "frames": len(f), "malicious": int((lab.frame_label == "malicious").sum()),
                     "queue_drops": int((drops.drop_kind == "queue_drop").sum()),
                     "expired_drops": int((drops.drop_kind == "expired_drop").sum()),
                     "validation": v["overall"], "PASS/NA/WARN/FAIL": f"{st.get('PASS', 0)}/{st.get('NA', 0)}/{st.get('WARN', 0)}/{st.get('FAIL', 0)}",
                     "size_MB": round(size / 1e6, 1), "session": sid})
    print("## Scenario matrix and scalability runs\n")
    print(table(pd.DataFrame(rows)), "\n")

    print("## Campaign ground truth (campaigns.csv, campaign_membership.csv, campaign_overlap.csv)\n")
    for name in ["s3_simultaneous_replay", "s3_small_stagger_replay", "s3_large_stagger_replay",
                 "s3_partial_overlap_replay", "s3_random_delay_replay", "s2_independent_replay", "s3_mixed_replay"]:
        sid = sessions.get(name)
        if not sid:
            continue
        gt = data / "ground_truth" / sid
        c = pd.read_csv(gt / "campaigns.csv", **LIT)
        m = pd.read_csv(gt / "campaign_membership.csv", **LIT)
        o = pd.read_csv(gt / "campaign_overlap.csv", **LIT)
        print(f"### {name} ({sid})\n")
        print(table(c[["campaign_id", "attack_type", "coordinated", "delay_pattern", "vehicle_count", "campaign_start",
                       "campaign_end", "campaign_duration_s", "min_inter_vehicle_delay_s", "max_inter_vehicle_delay_s",
                       "common_overlap_s"]]), "\n")
        print(table(m[["campaign_id", "vehicle_id", "attack_id", "vehicle_attack_start", "vehicle_attack_end",
                       "relative_delay_s"]]), "\n")
        if len(o):
            print(table(o[["campaign_id", "vehicle_a", "vehicle_b", "start_delay_s", "overlap_s", "overlap_ratio_min", "jaccard"]]), "\n")

    print("## Configured vs observed delay per timing layer (relative to the first member)\n")
    lay = []
    for name, sid in sessions.items():
        v = json.loads((data / "validation" / sid / "validation_report.json").read_text())
        for cid, members in v["info"].get("campaign_delay_by_layer", {}).items():
            for x in members:
                lay.append({"run": name, "campaign": cid, "vehicle": x["vehicle_id"],
                            "configured_s": f"{x['configured_delay_s']:.6f}",
                            "first_generation_s": f"{x['first_generation_delay_s']:.6f}",
                            "first_transmission_s": "" if x["first_transmission_delay_s"] is None else f"{x['first_transmission_delay_s']:.6f}",
                            "first_capture_s": "" if x["first_capture_delay_s"] is None else f"{x['first_capture_delay_s']:.6f}"})
    print(table(pd.DataFrame(lay)), "\n")

    print("## Frame -> attack -> campaign traces (s3_mixed_replay)\n")
    sid = sessions.get("s3_mixed_replay")
    if sid:
        f = pd.read_csv(data / "raw" / sid / "can_frames.csv", **LIT)
        lab = pd.read_csv(data / "ground_truth" / sid / "frame_labels.csv", **LIT)
        att = pd.read_csv(data / "ground_truth" / sid / "attacks.csv", **LIT)
        rep = pd.read_csv(data / "ground_truth" / sid / "replay_map.csv", **LIT)
        j = f.merge(lab, on=["frame_uid", "session_id"]).merge(att[["attack_id", "configured_start", "configured_end"]], on="attack_id", how="left")
        picks = pd.concat([j[j.campaign_id == c].head(1) for c in ["C201", "C202"]] +
                          [j[(j.campaign_id == "C201") & (j.vehicle_id == "vehicle_02")].head(1), j[j.frame_label == "benign"].head(1)])
        picks = picks.merge(rep[["replay_frame_uid", "original_frame_uid"]], left_on="frame_uid", right_on="replay_frame_uid", how="left")
        print(table(picks[["frame_uid", "vehicle_id", "timestamp", "transmission_timestamp", "can_id", "payload", "frame_label",
                           "attack_id", "campaign_id", "configured_start", "original_frame_uid"]].fillna("")), "\n")

    print("## Timing statistics per run (all vehicles; us)\n")
    ts = []
    for name, sid in sessions.items():
        v = json.loads((data / "validation" / sid / "validation_report.json").read_text())["info"]
        q = pd.DataFrame(v["queue_delay_us"]).T
        c = pd.DataFrame(v.get("capture_offset_us", {})).T
        ts.append({"run": name, "queue_delay_p99_max_over_vehicles": q.p99.max(), "queue_delay_max": q["max"].max(),
                   "capture_offset_p50_median": c.p50.median() if len(c) else "",
                   "capture_offset_p99_max": c.p99.max() if len(c) else "", "capture_offset_max": c["max"].max() if len(c) else "",
                   "peak_bus_util_100ms": max(x["peak_100ms"] for x in v["bus_utilization"].values())})
    print(table(pd.DataFrame(ts)), "\n")
    print(f"data/ total: {du(data)}")


if __name__ == "__main__":
    main()
