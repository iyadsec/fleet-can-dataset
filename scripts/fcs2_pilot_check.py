"""M12 pilot check: does fleetcan-sim-2.0 contain behavioural and temporal coordination, and more benign variety?

Read-only analysis of generated sessions (default ~/fcs2_pilot) and, for comparison, of fleetcan-sim-scale-1.0
sessions in data/. Behaviour is measured with the original FLEET-GUARD 24-D window descriptor
(analysis/descriptor_extractor.py, 100 frames, stride 50, transmission time) on each attacked vehicle's windows that
contain >= MIN_ATTACK attack frames; one attack is represented by the mean descriptor of those windows (each feature
standardised with the pooled benign statistics of its vehicle profile). Similarity = cosine of the representations.

Usage: .venv/bin/python scripts/fcs2_pilot_check.py [--data ~/fcs2_pilot] [--v1 SID ...]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analysis.descriptor_extractor import build_descriptors, load_observations, window_frame_uids  # noqa: E402
from analysis.descriptor_schema import FEATURE_COLUMNS                                           # noqa: E402
from src import storage                                                                          # noqa: E402

MIN_ATTACK = 20


def session(data: Path, sid: str):
    obs = load_observations(data / "raw" / sid)
    desc = build_descriptors(obs)
    lab = storage.read_table(data / "ground_truth" / sid / "frame_labels", columns=["frame_uid", "attack_id"]).set_index("frame_uid")
    members = window_frame_uids(obs)
    aid = []
    for d in desc.descriptor_id:
        ids = lab.loc[members[d], "attack_id"]
        vc = ids[ids != "None"].value_counts()
        aid.append(vc.index[0] if len(vc) and vc.iloc[0] >= MIN_ATTACK else "None")
    desc["attack_id"] = aid
    meta = json.loads((data / "metadata" / sid / "metadata.json").read_text())
    prof = {v["vehicle_id"]: v["profile"][-1] for v in meta["fleet"]["vehicles"]}
    desc["profile"] = desc.vehicle_id.map(prof)
    att = storage.read_table(data / "ground_truth" / sid / "attacks")
    ctl = data / "ground_truth" / sid / "campaign_controller.parquet"
    ctl = pd.read_parquet(ctl) if ctl.exists() else pd.DataFrame()
    return desc, att, ctl, meta


def representations(desc: pd.DataFrame) -> dict:
    X = desc[FEATURE_COLUMNS].astype(float)
    ben = desc.attack_id == "None"
    mu = X[ben].groupby(desc.profile[ben]).mean()
    sd = X[ben].groupby(desc.profile[ben]).std().replace(0, np.nan)
    Z = (X - mu.loc[desc.profile].values) / sd.loc[desc.profile].values
    Z = Z.fillna(0.0)
    out = {}
    for a, g in Z[~ben].groupby(desc.attack_id[~ben]):
        out[a] = g.mean().to_numpy()
    return out


def cos(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else np.nan


def pairs(reps: dict, groups: dict) -> list[tuple]:
    """(group key or None, cosine) for every attack pair; key set when both attacks belong to the same group."""
    out = []
    for a, b in itertools.combinations(sorted(reps), 2):
        ga, gb = groups.get(a), groups.get(b)
        out.append((ga if ga is not None and ga == gb else None, cos(reps[a], reps[b])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path.home() / "fcs2_pilot"))
    ap.add_argument("--v1", nargs="*", default=[])
    a = ap.parse_args()
    data = Path(a.data)
    rows, timing, benign = [], [], []
    for md in sorted((data / "metadata").iterdir()):
        sid = md.name
        meta = json.loads((md / "metadata.json").read_text())
        tags = meta["tags"]
        desc, att, ctl, _ = session(data, sid)
        b = desc[desc.attack_id == "None"]
        for v, g in b.groupby("vehicle_id"):
            benign.append({"profile": g.profile.iloc[0], "unique_ids_mean": g.unique_can_id_count.mean(),
                           "entropy_mean": g.can_id_entropy.mean(), "iat_std_mean": g.std_inter_arrival_time.mean()})
        if not len(ctl) and tags["scenario"] not in ("S1",):
            continue
        reps = representations(desc)
        if len(ctl):
            grp = {r.attack_id: (r.campaign_id if r.coordinated == "true" else (r.procedure_id if tags["hard_negative"] in ("behaviour", "both") else None))
                   for r in ctl.itertuples()}
            for key, c in pairs(reps, grp):
                kind = ("same_campaign" if key and key.startswith("C_") else "shared_procedure_independent" if key else "different_procedure")
                rows.append({"session": sid, "scenario": tags["scenario"], "strength": tags.get("strength"),
                             "hard_negative": tags.get("hard_negative"), "family": tags["family"], "pair": kind, "cosine": c})
            # timing: configured member start vs first observable attack frame
            fr = storage.read_table(data / "raw" / sid / "can_frames", columns=["frame_uid", "tx_end_ns"])
            lab = storage.read_table(data / "ground_truth" / sid / "frame_labels", columns=["frame_uid", "attack_id"])
            first = fr.merge(lab, on="frame_uid").query("attack_id != 'None'").assign(t=lambda d: d.tx_end_ns.astype("int64")).groupby("attack_id").t.min()
            for r in ctl.itertuples():
                if r.attack_id in first.index:
                    timing.append({"session": sid, "pattern": r.controller_pattern, "latency_ms": int(r.delivery_latency_ns) / 1e6,
                                   "onset_ms": (first[r.attack_id] - int(r.start_ns)) / 1e6,
                                   "trigger_wait_s": (int(r.trigger_ns) - int(r.arm_ns)) / 1e9 if str(r.trigger_ns) not in ("", "nan") else None})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print("== behavioural similarity of attack pairs (cosine of mean standardised 24-D descriptors) ==")
    print(df.groupby(["pair"]).cosine.describe()[["count", "mean", "25%", "50%", "75%"]].round(3))
    print(df[df.pair == "same_campaign"].groupby("strength").cosine.describe()[["count", "mean", "50%"]].round(3))
    print(df[df.scenario.str.startswith("S2")].groupby(["hard_negative", "pair"]).cosine.describe()[["count", "mean", "50%"]].round(3))
    print(df.groupby(["family", "pair"]).cosine.median().unstack().round(3))
    t = pd.DataFrame(timing)
    print("== timing ==")
    print(t.groupby("pattern")[["latency_ms", "onset_ms"]].describe().round(2).iloc[:, [1, 5, 7, 9, 13, 15]])
    print("condition trigger wait (s):", t.trigger_wait_s.dropna().describe().round(2).to_dict())
    print("== benign variety per profile (window level) ==")
    print(pd.DataFrame(benign).groupby("profile").mean().round(3))
    if a.v1:
        print("== fleetcan-sim-scale-1.0 comparison ==")
        v1 = []
        for sid in a.v1:
            desc, att, _, meta = session(ROOT / "data", sid)
            reps = representations(desc)
            camp = dict(zip(att.attack_id, att.campaign_id))
            coord = meta["tags"]["scenario"] == "S3"
            for key, c in pairs(reps, {k: v for k, v in camp.items() if coord}):
                v1.append({"session": sid, "pair": "same_campaign" if key else "different_campaign", "cosine": c})
        print(pd.DataFrame(v1).groupby(["session", "pair"]).cosine.median().round(3))


if __name__ == "__main__":
    main()
