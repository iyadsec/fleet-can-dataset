"""Summarise the latest Milestone 2 sessions from their saved files (nothing is recomputed by hand).

Usage: .venv/bin/python scripts/summarize_m2.py [--data data] > m2_summary.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cantools
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
LIT = dict(keep_default_na=False, na_filter=False, dtype=str)
DBC = cantools.database.load_file(str(Path(__file__).resolve().parent.parent / "dbc/generic_vehicle.dbc"))


def latest(data: Path, exp_id: str) -> str | None:
    s = sorted(p.name for p in (data / "raw").glob(f"{exp_id}_*"))
    return s[-1] if s else None


def load(data, sid):
    raw, gt, md = data / "raw" / sid, data / "ground_truth" / sid, data / "metadata" / sid
    f = pd.read_csv(raw / "can_frames.csv", **LIT)
    lab = pd.read_csv(gt / "frame_labels.csv", **LIT)
    f = f.merge(lab[["frame_uid", "frame_label", "attack_id", "attack_type", "frame_source"]], on="frame_uid")
    return (f, pd.read_csv(gt / "attacks.csv", **LIT), pd.read_csv(gt / "attack_frames.csv", **LIT),
            pd.read_csv(gt / "replay_map.csv", **LIT), pd.read_csv(gt / "dropped_frames.csv", **LIT),
            json.loads((md / "bus_stats.json").read_text()),
            json.loads((data / "validation" / sid / "validation_report.json").read_text()))


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    data = Path(ap.parse_args().data)

    print("## Flooding intensity sweep (vehicle_01, ID 0x000, window 10-15 s)\n")
    rows = []
    for name in ["flood_low", "flood_medium", "flood_near_sat", "flood_over_sat"]:
        sid = latest(data, f"M2_{name.upper()}")
        if not sid:
            continue
        f, att, _, _, drops, bs, val = load(data, sid)
        a = att.iloc[0]
        w = next(x for x in bs["attack_windows"] if x["attack_id"] == a.attack_id)
        b, d = w["baseline"], w["during"]
        qd = d["benign_queue_delay_us"]
        rows.append({
            "run": name, "requested_rate_hz": float(a.requested_rate_hz), "generated": a.generated,
            "transmitted": a.transmitted, "attack_dropped": a.dropped, "captured": a.captured,
            "observed_rate_hz": float(a.observed_rate_hz),
            "util_baseline": round(b["utilization"], 3), "util_during": round(d["utilization"], 3),
            "benign_gen_during": d["benign_generated"], "benign_tx_during": d["benign_transmitted"],
            "benign_dropped_during": d["benign_dropped"],
            "benign_qdelay_mean_us": round(qd.get("mean", 0), 1), "benign_qdelay_p99_us": round(qd.get("p99", 0), 1),
            "benign_qdelay_max_us": round(qd.get("max", 0), 1),
            "baseline_qdelay_max_us": round(b["benign_queue_delay_us"].get("max", 0), 1),
            "validation": val["overall"], "session": sid})
    print(md_table(pd.DataFrame(rows)), "\n")

    for name, title in [("fuzzy_id", "Fuzzy: random IDs"), ("fuzzy_payload", "Fuzzy: random payloads (ID 0x120)"),
                        ("fuzzy_both", "Fuzzy: random IDs + payloads")]:
        sid = latest(data, f"M2_{name.upper()}")
        if not sid:
            continue
        f, att, afr, *_ = load(data, sid)
        a = att.iloc[0]
        m = f[f.frame_label == "malicious"].head(8)
        print(f"### {title} ({sid}): generated={a.generated} transmitted={a.transmitted} dropped={a.dropped} "
              f"distinct IDs={f[f.frame_label == 'malicious'].can_id.nunique()}\n")
        print(md_table(m[["timestamp", "transmission_timestamp", "queue_delay_us", "can_id", "dlc", "payload",
                          "frame_label", "attack_type", "attack_id"]]), "\n")

    for name in ["malfunction_inject", "malfunction_replace"]:
        sid = latest(data, f"M2_{name.upper()}")
        if not sid:
            continue
        f, att, afr, *_ = load(data, sid)
        m = f[f.frame_label == "malicious"].merge(afr[["frame_uid", "signal", "expected_value", "malicious_value",
                                                        "original_payload"]], on="frame_uid").head(6)
        m["decoded"] = [DBC.decode_message(int(c, 16), bytes.fromhex(p)) for c, p in zip(m.can_id, m.payload)]
        print(f"### {name} ({sid}) attack rows: {att[['attack_id', 'configured_start', 'configured_end', 'generated', 'transmitted', 'captured']].to_dict('records')}\n")
        print(md_table(m[["timestamp", "can_id", "frame_source", "payload", "signal", "expected_value",
                          "malicious_value", "original_payload", "decoded"]]), "\n")
        if name == "malfunction_inject":
            t = f.sim_time_ns.astype("int64")
            legit = f[(f.can_id == "0x120") & (f.frame_label == "benign") & t.between(10_000_000_000, 10_050_000_000)]
            print("Interleaved legitimate 0x120 frames in the same window:\n")
            print(md_table(legit[["timestamp", "can_id", "frame_source", "payload"]].head(3)), "\n")

    sid = latest(data, "M2_REPLAY")
    if sid:
        f, att, afr, rep, *_ = load(data, sid)
        print(f"### Replay ({sid}): {att[['attack_id', 'configured_start', 'configured_end', 'generated', 'transmitted', 'captured']].to_dict('records')}\n")
        print(md_table(rep.head(8)), "\n")


if __name__ == "__main__":
    main()
