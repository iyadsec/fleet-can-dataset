"""Milestone 5.1: real-time vs fast-mode equivalence under bus saturation (queueing, queue drops, expiry).

Compares M51_SAT_FLOOD_OVERSAT_REALTIME and M51_SAT_FLOOD_OVERSAT_FAST:
  * every logical table + descriptors (scripts/compare_sessions.py)
  * CAN arbitration order: per vehicle, the sequence (can_id, payload, generation, tx_start, tx_end) in
    transmission order must be identical
  * counts per run (attack / benign): generated, transmitted, queue-dropped, expired, captured
Capture timestamps are excluded from equality by design.

Usage: .venv/bin/python scripts/m51_saturation_report.py [--data data]
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_sessions import compare  # noqa: E402
from src import storage  # noqa: E402

RT, FAST = "M51_SAT_FLOOD_OVERSAT_REALTIME", "M51_SAT_FLOOD_OVERSAT_FAST"


def counts(data: Path, sid: str) -> list[dict]:
    meta = json.loads((data / "metadata" / sid / "metadata.json").read_text())
    f = storage.read_table(data / "raw" / sid / "can_frames", columns=["frame_uid", "vehicle_id"])
    lab = storage.read_table(data / "ground_truth" / sid / "frame_labels", columns=["frame_uid", "frame_label"])
    f = f.merge(lab, on="frame_uid")
    d = storage.read_table(data / "ground_truth" / sid / "dropped_frames")
    rows = []
    for cls in ("malicious", "benign"):
        cap = int((f.frame_label == cls).sum())
        dd = d[d.frame_label == cls]
        q, x = int((dd.drop_kind == "queue_drop").sum()), int((dd.drop_kind == "expired_drop").sum())
        rows.append({"run": "real-time" if sid == RT else "fast", "class": "attack" if cls == "malicious" else "benign",
                     "generated": cap + q + x, "transmitted": cap, "queue_dropped": q, "expired": x, "captured": cap})
    t = meta["transport_stats"]["total"]
    rows.append({"run": "real-time" if sid == RT else "fast", "class": "ALL (transport)",
                 "generated": sum(v["generated"] for v in meta["can"]["bus_totals"].values()),
                 "transmitted": sum(v["transmitted"] for v in meta["can"]["bus_totals"].values()),
                 "queue_dropped": sum(v["queue_dropped"] for v in meta["can"]["bus_totals"].values()),
                 "expired": sum(v["expired_dropped"] for v in meta["can"]["bus_totals"].values()),
                 "captured": t["captured"]})
    return rows


def arbitration_sequence(data: Path, sid: str) -> dict:
    f = storage.read_table(data / "raw" / sid / "can_frames",
                           columns=["vehicle_id", "can_id", "payload", "sim_time_ns", "tx_start_ns", "tx_end_ns"])
    f["tx_start_ns"] = f.tx_start_ns.astype("int64")
    return {v: list(map(tuple, g.sort_values("tx_start_ns")[["can_id", "payload", "sim_time_ns", "tx_start_ns", "tx_end_ns"]]
                        .astype(str).to_numpy())) for v, g in f.groupby("vehicle_id")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    data = Path(ap.parse_args().data)
    res = compare(data, RT, FAST)
    print("## Logical equivalence (real-time vs fast)\n")
    for k, v in res.items():
        print(f"- {k}: {v}")
    seq_rt, seq_fast = arbitration_sequence(data, RT), arbitration_sequence(data, FAST)
    same_order = seq_rt == seq_fast
    print(f"- arbitration order (per vehicle, transmission sequence): "
          f"{'IDENTICAL' if same_order else 'DIFFERENT'} ({ {v: len(s) for v, s in seq_rt.items()} } frames)")
    rows = counts(data, RT) + counts(data, FAST)
    df = pd.DataFrame(rows)
    print("\n## Counts\n")
    print("| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns))
    for r in df.itertuples(index=False):
        print("| " + " | ".join(str(x) for x in r) + " |")
    for sid in (RT, FAST):
        m = json.loads((data / "metadata" / sid / "metadata.json").read_text())
        bstats = json.loads((data / "metadata" / sid / "bus_stats.json").read_text())
        w = bstats["attack_windows"][0]["during"]
        print(f"\n{sid}: mode={m['simulation']['execution']['mode']} wall={m['simulation']['execution']['wall_seconds']} s, "
              f"flow_control_timeouts={m['simulation']['execution']['flow_control_timeouts']}, attacked-bus utilization during "
              f"attack={w['utilization']:.4f}, benign generated/transmitted/expired during attack="
              f"{w['benign_generated']}/{w['benign_transmitted']}/{w.get('benign_expired_dropped')}, "
              f"benign queue delay max={w['benign_queue_delay_us'].get('max')} us")
    ok = all(v.startswith("IDENTICAL") for v in res.values()) and same_order
    print(f"\nRESULT: {'ALL LOGICAL OUTPUTS IDENTICAL' if ok else 'DIFFERENCES FOUND - STOP'}")


if __name__ == "__main__":
    main()
