"""Compare the deterministic content of two sessions (e.g. a Milestone 2 run and its re-run on newer code).

Compared: per frame (vehicle, generation ns, CAN ID, payload, modelled tx_start/tx_end, label, attack_id)
and per drop (vehicle, generation ns, CAN ID, payload, reason). Capture times are host-dependent and ignored.
Usage: .venv/bin/python scripts/compare_sessions.py <session_a> <session_b> [--data data]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import storage  # noqa: E402
FRAME_KEY = ["vehicle_id", "sim_time_ns", "can_id", "payload", "tx_start_ns", "tx_end_ns", "frame_label", "attack_id"]
DROP_KEY = ["vehicle_id", "sim_time_ns", "can_id", "payload", "reason"]


def frames(data, sid):
    f = storage.read_table(data / "raw" / sid / "can_frames")
    lab = storage.read_table(data / "ground_truth" / sid / "frame_labels")
    return f.merge(lab, on=["frame_uid", "session_id"])[FRAME_KEY].sort_values(FRAME_KEY).reset_index(drop=True)


def drops(data, sid):
    d = storage.read_table(data / "ground_truth" / sid / "dropped_frames")
    return d[DROP_KEY].sort_values(DROP_KEY).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    data = Path(a.data)
    fa, fb = frames(data, a.a), frames(data, a.b)
    da, db = drops(data, a.a), drops(data, a.b)
    same_f, same_d = fa.equals(fb), da.equals(db)
    print(f"frames: {len(fa)} vs {len(fb)} -> {'IDENTICAL' if same_f else 'DIFFERENT'}")
    print(f"drops : {len(da)} vs {len(db)} -> {'IDENTICAL' if same_d else 'DIFFERENT'}")
    if not same_f and len(fa) == len(fb):
        diff = (fa != fb).any(axis=1)
        print("first differing frame rows:\n", pd.concat([fa[diff].head(3), fb[diff].head(3)]))
    sys.exit(0 if same_f and same_d else 1)


if __name__ == "__main__":
    main()
