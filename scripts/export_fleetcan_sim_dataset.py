"""Export fleetcan-sim-scale-1.0 as fleetcan-sim-dataset-1.0: one labelled CAN log per session, in the style of the
HCRL/OCSLab car-hacking datasets (Timestamp, Arbitration_ID, DLC, Data, Class, SubClass), plus Vehicle_ID,
Attack_ID and Campaign_ID for the multi-vehicle setting, and plain summary tables.

The frozen source is only read; values are converted, never changed:
  Timestamp       = source tx_end_ns / 1e9, written with 9 decimals (seconds on the shared simulation clock;
                    the time the frame is complete on the vehicle's CAN bus)
  Arbitration_ID  = source can_id without the '0x' prefix (3 hex digits, 11-bit)
  Data            = source payload, bytes separated by a space
  Class/SubClass  = source frame_label / attack_type ('Normal' / 'Attack'; 'Normal', 'Flooding', 'Fuzzy',
                    'Malfunction', 'Replay')
Every session file is read back and compared with the source before it is accepted.

Usage: .venv/bin/python scripts/export_fleetcan_sim_dataset.py OUTPUT_DIR [--workers 2] [--sessions SID ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("FLEETCAN_SRC", REPO / "data"))
NAME = "fleetcan-sim-dataset-1.0"
SCENARIO_DIR = {"S0": "S0_Normal", "S1": "S1_Isolated_Attack", "S2": "S2_Independent_Attacks",
                "S3": "S3_Coordinated_Campaign", "S3-mixed": "S3_Mixed", "S3-probe": "S3_Probe"}
SUBCLASS = {"flooding": "Flooding", "fuzzy": "Fuzzy", "malfunction": "Malfunction", "replay": "Replay"}
SCHEMA = pa.schema([("Timestamp", pa.string()), ("Vehicle_ID", pa.string()), ("Arbitration_ID", pa.string()),
                    ("DLC", pa.int64()), ("Data", pa.string()), ("Class", pa.string()), ("SubClass", pa.string()),
                    ("Attack_ID", pa.string()), ("Campaign_ID", pa.string())])
WRITE = pacsv.WriteOptions(quoting_style="none", include_header=False)


def write_csv(table: pa.Table, out_stream):
    """Plain CSV like the HCRL/OCSLab files: unquoted header, unquoted values (no value contains ',' or '"')."""
    out_stream.write((",".join(table.schema.names) + "\n").encode())
    pacsv.write_csv(table, out_stream, WRITE)
NS = 1_000_000_000


def secs(ns) -> np.ndarray:
    q, r = np.divmod(np.asarray(ns, dtype=np.int64), NS)
    return np.char.add(np.char.add(q.astype(str), "."), np.char.zfill(r.astype(str), 9))


def rel_path(rec: dict, ext: str) -> str:
    att = "Normal" if rec["attack"] == "none" else SUBCLASS[rec["attack"]]
    return f"{ext}/{SCENARIO_DIR[rec['scenario']]}/{att}/N{rec['N']:03d}/{rec['session_id']}.{'csv.gz' if ext == 'CSV' else 'parquet'}"


def atomic(path: Path, writer):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    writer(tmp)
    os.replace(tmp, path)


def session(args) -> dict:
    rec, out = args
    sid = rec["session_id"]
    f = pq.read_table(SRC / "raw" / sid / "can_frames.parquet", columns=["frame_uid", "tx_end_ns", "vehicle_id", "can_id", "dlc", "payload"])
    lab = pq.read_table(SRC / "ground_truth" / sid / "frame_labels.parquet", columns=["frame_uid", "frame_label", "attack_type", "attack_id", "campaign_id"])
    assert lab.column("frame_uid").equals(f.column("frame_uid")), f"{sid}: labels not aligned"
    n = f.num_rows
    mal = pc.equal(lab.column("frame_label"), "malicious").to_numpy(zero_copy_only=False)
    at = lab.column("attack_type").to_numpy(zero_copy_only=False).astype(str)
    pay = f.column("payload").to_numpy(zero_copy_only=False)
    data = [" ".join(p[i:i + 2] for i in range(0, len(p), 2)) for p in pay]
    table = pa.Table.from_arrays([
        pa.array(secs(f.column("tx_end_ns").to_numpy())), f.column("vehicle_id"),
        pc.utf8_slice_codeunits(f.column("can_id"), 2), f.column("dlc"), pa.array(data, pa.string()),
        pa.array(np.where(mal, "Attack", "Normal")),
        pa.array(np.where(mal, np.vectorize(lambda x: SUBCLASS.get(x, x), otypes=[object])(at), "Normal").astype(str)),
        pa.array(np.where(mal, lab.column("attack_id").to_numpy(zero_copy_only=False).astype(str), "")),
        pa.array(np.where(mal, lab.column("campaign_id").to_numpy(zero_copy_only=False).astype(str), ""))], schema=SCHEMA)
    csv_p, pq_p = out / rel_path(rec, "CSV"), out / rel_path(rec, "Parquet")

    def wcsv(tmp):
        with pa.CompressedOutputStream(str(tmp), "gzip") as o:
            write_csv(table, o)
    atomic(csv_p, wcsv)
    atomic(pq_p, lambda tmp: pq.write_table(table, tmp, compression="zstd", compression_level=9))
    # read back and compare with the source values
    conv = pacsv.ConvertOptions(column_types={c.name: c.type for c in SCHEMA}, strings_can_be_null=False, null_values=[])
    back_csv = pacsv.read_csv(str(csv_p), convert_options=conv)
    back_pq = pq.read_table(pq_p)
    ok_csv, ok_pq = back_csv.equals(table), back_pq.equals(table)
    t_back = pc.cast(pc.replace_substring(back_csv.column("Timestamp"), ".", ""), pa.int64()).to_numpy()
    d_back = pc.replace_substring(back_csv.column("Data"), " ", "")
    checks = {
        "rows": n, "csv_equal": ok_csv, "parquet_equal": ok_pq,
        "timestamp_equals_source": bool((t_back == f.column("tx_end_ns").to_numpy()).all()),
        "data_equals_source": bool(pc.all(pc.equal(d_back, f.column("payload"))).as_py()),
        "id_equals_source": bool(pc.all(pc.equal(pc.binary_join_element_wise("0x", back_csv.column("Arbitration_ID"), ""), f.column("can_id"))).as_py()),
        "dlc_equals_source": bool(back_csv.column("DLC").equals(f.column("dlc"))),
        "vehicle_equals_source": bool(back_csv.column("Vehicle_ID").equals(f.column("vehicle_id"))),
        "attack_rows": int(mal.sum()),
        "attack_rows_with_ids": int(((table.column("Attack_ID").to_numpy(zero_copy_only=False) != "") & mal).sum()),
    }
    # replay links (observable timestamps of original and replayed frames)
    links = None
    if rec["attack"] == "replay":
        rm = pq.read_table(SRC / "ground_truth" / sid / "replay_map.parquet", columns=["attack_id", "replay_frame_uid", "original_frame_uid"]).to_pandas()
        idx = lambda s: s.str.rsplit("-F", n=1).str[1].astype("int64").to_numpy()   # noqa: E731
        tx = f.column("tx_end_ns").to_numpy()
        veh = f.column("vehicle_id").to_numpy(zero_copy_only=False)
        cid = f.column("can_id").to_numpy(zero_copy_only=False)
        ri, oi = idx(rm.replay_frame_uid), idx(rm.original_frame_uid)
        links = out / ".links" / f"{sid}.parquet"
        lk = pd.DataFrame({"Session_ID": sid, "Attack_ID": rm.attack_id.values, "Vehicle_ID": veh[ri],
                              "Arbitration_ID": [c[2:] for c in cid[ri]], "Original_Timestamp": secs(tx[oi]),
                              "Replay_Timestamp": secs(tx[ri])})
        links.parent.mkdir(parents=True, exist_ok=True)
        lk.to_parquet(links, index=False)
        links = str(links)
    by_v = pd.DataFrame({"v": f.column("vehicle_id").to_numpy(zero_copy_only=False), "m": mal})
    vstats = by_v.groupby("v").m.agg(["size", "sum"])
    first_last = {}
    if mal.any():
        tx = f.column("tx_end_ns").to_numpy()
        aid = lab.column("attack_id").to_numpy(zero_copy_only=False)
        s = pd.Series(tx[mal]).groupby(aid[mal])
        first_last = {k: (int(a), int(b), int(c)) for k, a, b, c in zip(s.min().index, s.min(), s.max(), s.size())}
    return {"session_id": sid, "checks": checks, "csv": rel_path(rec, "CSV"), "parquet": rel_path(rec, "Parquet"),
            "vehicle_frames": {v: (int(r["size"]), int(r["sum"])) for v, r in vstats.iterrows()},
            "attack_frames": first_last, "links": links}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--sessions", nargs="*")
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser() / NAME
    recs = [json.loads(x) for x in (SRC / "final" / "fleetcan-sim-scale-1.0" / "manifest.jsonl").read_text().splitlines() if x.strip()]
    if a.sessions:
        recs = [r for r in recs if r["session_id"] in a.sessions]
    recs.sort(key=lambda r: r["session_id"])
    results = []
    with Pool(a.workers, maxtasksperchild=10) as pool:
        for i, res in enumerate(pool.imap(session, [(r, out) for r in recs]), 1):
            c = res["checks"]
            bad = [k for k, v in c.items() if v is False] + (["attack_ids"] if c["attack_rows_with_ids"] != c["attack_rows"] else [])
            if bad:
                sys.exit(f"STOP: {res['session_id']} failed {bad}")
            results.append(res)
            if i % 25 == 0 or i == len(recs):
                print(f"{i}/{len(recs)} sessions exported and verified", flush=True)
    write_tables(out, recs, {r["session_id"]: r for r in results})


def write_tables(out: Path, recs, res):
    rows_s, rows_v, rows_a, rows_c, rows_m, links = [], [], [], [], [], []
    for r in recs:
        sid = r["session_id"]
        x = res[sid]
        meta = json.loads((SRC / "metadata" / sid / "metadata.json").read_text())
        fleet = meta["fleet"]["vehicles"]
        vf = x["vehicle_frames"]
        att = pq.read_table(SRC / "ground_truth" / sid / "attacks.parquet").to_pandas()
        camp = pq.read_table(SRC / "ground_truth" / sid / "campaigns.parquet").to_pandas()
        mem = pq.read_table(SRC / "ground_truth" / sid / "campaign_membership.parquet").to_pandas()
        attacked = set(att.vehicle_id)
        rows_s.append({"Session_ID": sid, "Configuration_ID": r["configuration_id"], "Repetition": r["repetition"],
                       "Scenario": r["scenario"], "Attack_Type": "Normal" if r["attack"] == "none" else SUBCLASS[r["attack"]],
                       "Fleet_Size": r["N"], "Campaign_Size": r["K"], "Timing_Pattern": r["temporal_pattern"],
                       "Independent_Timing": r["independent_timing"], "Member_Selection": meta["tags"].get("member_selection", ""),
                       "Duration_s": r["duration_s"], "Seed": r["seed"], "Vehicles": len(fleet), "Attacked_Vehicles": len(attacked),
                       "Frames": x["checks"]["rows"], "Normal_Frames": x["checks"]["rows"] - x["checks"]["attack_rows"],
                       "Attack_Frames": x["checks"]["attack_rows"], "Attacks": len(att), "Campaigns": len(camp),
                       "CSV_File": x["csv"], "Parquet_File": x["parquet"]})
        for v in fleet:
            vid = v["vehicle_id"]
            rows_v.append({"Session_ID": sid, "Vehicle_ID": vid, "Profile": v["profile"].replace("profile_", ""),
                           "ECU_Switch_On_s": int(v["switch_on_ns"]) / NS, "Attacked": vid in attacked,
                           "Frames": vf[vid][0], "Attack_Frames": vf[vid][1]})
        for a in att.itertuples():
            fl = x["attack_frames"].get(a.attack_id, (None, None, 0))
            rows_a.append({"Session_ID": sid, "Attack_ID": a.attack_id, "Vehicle_ID": a.vehicle_id,
                           "Attack_Type": SUBCLASS[a.attack_type], "Campaign_ID": a.campaign_id,
                           "Start_s": int(a.start_ns) / NS, "End_s": int(a.end_ns) / NS,
                           "Rate_Hz": a.requested_rate_hz if a.requested_rate_hz not in ("", None) else "",
                           "Attack_Frames": fl[2], "First_Attack_Frame_s": fl[0] / NS if fl[0] is not None else "",
                           "Last_Attack_Frame_s": fl[1] / NS if fl[1] is not None else "", "Parameters": a.params})
        for c in camp.itertuples():
            rows_c.append({"Session_ID": sid, "Campaign_ID": c.campaign_id, "Coordinated": str(c.coordinated).lower() == "true",
                           "Attack_Type": SUBCLASS[c.attack_type], "Delay_Pattern": c.delay_pattern,
                           "Vehicles": int(c.vehicle_count), "Start_s": int(c.campaign_start_ns) / NS, "End_s": int(c.campaign_end_ns) / NS})
        for m in mem.itertuples():
            rows_m.append({"Session_ID": sid, "Campaign_ID": m.campaign_id, "Vehicle_ID": m.vehicle_id, "Attack_ID": m.attack_id,
                           "Start_s": int(m.vehicle_attack_start_ns) / NS, "End_s": int(m.vehicle_attack_end_ns) / NS,
                           "Relative_Delay_s": int(m.relative_delay_ns) / NS})
        if x["links"] is not None:
            links.append(x["links"])
    for name, rows in (("sessions.csv", rows_s), ("vehicles.csv", rows_v), ("attacks.csv", rows_a),
                       ("campaigns.csv", rows_c), ("campaign_members.csv", rows_m)):
        df = pd.DataFrame(rows)
        atomic(out / name, lambda tmp: df.to_csv(tmp, index=False))
    n_links = 0

    def wlinks(tmp):
        nonlocal n_links
        with pa.CompressedOutputStream(str(tmp), "gzip") as o:
            for i, p in enumerate(links):
                t = pq.read_table(p)
                n_links += t.num_rows
                if i == 0:
                    o.write((",".join(t.schema.names) + "\n").encode())
                pacsv.write_csv(t, o, WRITE)
    atomic(out / "replay_links.csv.gz", wlinks)
    for p in links:
        os.remove(p)
    (out / ".links").rmdir()
    s = pd.DataFrame(rows_s)
    print(json.dumps({"sessions": len(s), "frames": int(s.Frames.sum()), "attack_frames": int(s.Attack_Frames.sum()),
                      "vehicles": len(rows_v), "attacks": len(rows_a), "campaigns": len(rows_c), "replay_links": n_links}))


if __name__ == "__main__":
    main()
