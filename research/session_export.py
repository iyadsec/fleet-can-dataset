"""Per-session export + validation for fleetcan-sim-research-1.0 (one streaming pass per session; bounded memory).

For one frozen source session this module
  1. validates the source schema, value formats, ordering and frame/label alignment;
  2. writes the detector-safe observations (Parquet + gzip CSV) and verifies both files read back identically;
  3. writes the non-observable per-frame bus timing (metadata), ground truth (+ frame_index join keys) and metadata;
  4. computes the original 24-D descriptors per vehicle and, separately, the evaluation-only window labels;
  5. derives observable attack/campaign/replay timing (clearly marked derived_*), never replacing configured values;
  6. writes per-session statistics into WORK/sessions/<sid>/ for the dataset-wide aggregation.
The source is opened read-only; nothing under the source data root is written.
"""

from __future__ import annotations

import json
import shutil
import warnings
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_schema import FEATURE_COLUMNS                                   # noqa: E402
from research.common import (BUS_TIMING_SCHEMA, NS, OBS_SCHEMA, RELEASE, SRC, STRIDE, WINDOW, WORK,  # noqa: E402
                             atomic_csv, atomic_json, atomic_parquet, read_csv_strict, sha256_file)
from research.descriptors import payload_matrix, trace_descriptors                       # noqa: E402

RAW_SCHEMA = [("frame_uid", "string"), ("session_id", "string"), ("scenario_id", "string"), ("timestamp", "string"),
              ("sim_time_ns", "int64"), ("transmission_timestamp", "string"), ("tx_start_ns", "int64"),
              ("tx_end_ns", "int64"), ("queue_delay_us", "string"), ("frame_bits", "int64"),
              ("capture_time_ns", "int64"), ("capture_offset_us", "string"), ("vehicle_id", "string"),
              ("channel", "string"), ("can_id", "string"), ("dlc", "int64"), ("payload", "string")]
LABEL_SCHEMA = ["frame_uid", "session_id", "frame_label", "attack_type", "campaign_id", "attack_id", "frame_source",
                "message_name"]
GT_TABLES = ["attacks", "campaigns", "campaign_membership", "campaign_overlap", "replay_map", "attack_frames",
             "dropped_frames"]
FRAME_LEVEL_GT = {"replay_map", "attack_frames"}           # written as .csv.gz; others as plain .csv
KS = (1, 5, 50)                                            # malicious-frame thresholds for the window-onset analysis
BIT_NS = 2000                                              # 500 kbit/s


def _np(t: pa.Table, c: str) -> np.ndarray:
    return t.column(c).to_numpy(zero_copy_only=False)


def _text_equals_ns(col, v: np.ndarray, decimals: int) -> bool:
    """True iff every text value is exactly v / 10**decimals written with `decimals` decimals (generator format):
    the dot sits `decimals` characters from the end and the digits without the dot parse to v."""
    n = len(v)
    length = pc.utf8_length(col).to_numpy(zero_copy_only=False)
    dot = pc.find_substring(col, ".").to_numpy(zero_copy_only=False)
    if n and not (dot == length - decimals - 1).all():
        return False
    digits = pc.cast(pc.replace_substring(col, ".", ""), pa.int64()).to_numpy(zero_copy_only=False)
    return bool((digits == v).all())


def _entropy(counts: np.ndarray) -> float:
    p = counts[counts > 0] / counts.sum()
    return float(-np.sum(p * np.log2(p))) if p.size else 0.0


def _stats(x: np.ndarray, prefix: str) -> dict:
    if x.size == 0:
        return {f"{prefix}_{k}": None for k in ("n", "mean", "std", "min", "p50", "max")}
    return {f"{prefix}_n": int(x.size), f"{prefix}_mean": float(x.mean()), f"{prefix}_std": float(x.std()),
            f"{prefix}_min": float(x.min()), f"{prefix}_p50": float(np.median(x)), f"{prefix}_max": float(x.max())}


def _index_of_uid(uids: np.ndarray, sid: str) -> tuple[np.ndarray, int]:
    """frame_uid '<sid>-F<9 digits>' -> frame_index; returns (indices, number of uids with a foreign prefix)."""
    s = pd.Series(uids, dtype="string")
    parts = s.str.rsplit("-F", n=1)
    bad = int((parts.str[0] != sid).sum())
    return parts.str[1].astype("int64").to_numpy(), bad


def _writable_rmtree(p: Path):
    for q in [p, *p.rglob("*")]:
        if q.is_dir():
            q.chmod(0o755)
    shutil.rmtree(p)


def _copy_tree(src: Path, dst: Path):
    """Copy file contents only (the frozen source is read-only; the release copy stays writable until release
    finalisation)."""
    for p in (dst, dst.with_name(dst.name + ".tmp")):
        if p.exists():
            _writable_rmtree(p)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copytree(src, tmp, copy_function=shutil.copyfile)
    for q in [tmp, *tmp.rglob("*")]:
        q.chmod(0o755 if q.is_dir() else 0o644)
    tmp.rename(dst)


def process(rec: dict) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)       # nan-statistics of all-NaN byte columns -> NaN
        return _process(rec)


def _process(rec: dict) -> dict:
    sid = rec["session_id"]
    t_start = time.time()
    out_obs_pq = RELEASE / "data" / "raw_parquet" / sid / "can_frames.parquet"
    out_obs_csv = RELEASE / "data" / "raw_csv" / sid / "can_frames.csv.gz"
    gt_dir = RELEASE / "data" / "ground_truth" / sid
    md_dir = RELEASE / "data" / "metadata" / sid
    de_dir = RELEASE / "data" / "descriptors" / sid
    wk = WORK / "sessions" / sid
    wk.mkdir(parents=True, exist_ok=True)

    meta = json.loads((SRC / "metadata" / sid / "metadata.json").read_text())
    tags = meta["tags"]
    bus = json.loads((SRC / "metadata" / sid / "bus_stats.json").read_text())
    fleet = {v["vehicle_id"]: v for v in meta["fleet"]["vehicles"]}
    prof = {v: fleet[v]["profile"].replace("profile_", "") for v in fleet}
    route_asg = {a["vehicle_id"]: a for a in meta["simulation"]["route_generation"].get("assignment", [])}
    duration_s = float(meta["simulation"]["duration_s_configured"])
    chk: dict = {"session_id": sid}

    # ------------------------------------------------------------------ source schema + formats
    raw_path = SRC / "raw" / sid / "can_frames.parquet"
    fr = pq.read_table(raw_path)
    n = fr.num_rows
    chk["raw_rows"] = n
    actual = [(f.name, str(f.type)) for f in fr.schema]
    chk["raw_schema_ok"] = actual == RAW_SCHEMA
    chk["raw_schema"] = ";".join(f"{a}:{b}" for a, b in actual) if actual != RAW_SCHEMA else "expected"
    chk["raw_null_values"] = int(sum(fr.column(c).null_count for c in fr.schema.names))
    ucol = fr.column("frame_uid")
    pre = f"{sid}-F"
    chk["frame_uid_equals_session_and_index"] = bool(
        pc.all(pc.starts_with(ucol, pre)).as_py() and (pc.utf8_length(ucol).to_numpy(zero_copy_only=False) == len(pre) + 9).all()
        and (pc.cast(pc.utf8_slice_codeunits(ucol, len(pre)), pa.int64()).to_numpy(zero_copy_only=False) == np.arange(n)).all())
    chk["session_id_constant"] = bool(pc.all(pc.equal(fr.column("session_id"), sid)).as_py())
    scen_vals = pc.unique(fr.column("scenario_id")).to_pylist()
    chk["scenario_id_values"] = ";".join(scen_vals)
    tx_end, sim, tx_start = _np(fr, "tx_end_ns"), _np(fr, "sim_time_ns"), _np(fr, "tx_start_ns")
    fbits, capt, dlc = _np(fr, "frame_bits"), _np(fr, "capture_time_ns"), _np(fr, "dlc")
    veh = _np(fr, "vehicle_id").astype(str)
    can_txt = _np(fr, "can_id").astype(str)
    pay_txt = _np(fr, "payload").astype(str)
    chan = _np(fr, "channel").astype(str)
    chk["timestamp_text_derivable"] = _text_equals_ns(fr.column("timestamp"), sim, 9)
    chk["transmission_timestamp_text_derivable"] = _text_equals_ns(fr.column("transmission_timestamp"), tx_end, 9)
    qd = tx_start - sim
    chk["queue_delay_text_derivable"] = bool((qd >= 0).all()) and _text_equals_ns(fr.column("queue_delay_us"), qd, 3)
    chk["capture_offset_empty"] = bool(pc.all(pc.equal(fr.column("capture_offset_us"), "")).as_py())
    chk["channel_matches_vehicle"] = bool(all((chan[veh == v] == fleet[v]["channel"]).all() for v in fleet))
    chk["vehicles_in_frames_equal_fleet"] = sorted(set(np.unique(veh))) == sorted(fleet)
    chk["can_id_format_violations"] = int((~pc.match_substring_regex(fr.column("can_id"), r"^0x[0-9A-F]{3}$").to_numpy()).sum())
    uq_txt, inv_txt = np.unique(can_txt, return_inverse=True)
    can_int = np.array([int(x, 16) for x in uq_txt], dtype=np.int64)[inv_txt]
    chk["can_id_over_11bit"] = int((can_int > 0x7FF).sum())
    chk["dlc_out_of_range"] = int(((dlc < 0) | (dlc > 8)).sum())
    plen = pc.utf8_length(fr.column("payload")).to_numpy(zero_copy_only=False)
    chk["payload_length_mismatch"] = int((plen != 2 * dlc).sum())
    chk["payload_non_hex"] = int((~pc.match_substring_regex(fr.column("payload"), r"^[0-9A-F]*$").to_numpy()).sum())
    lo = 47 + 8 * dlc
    hi = lo + (34 + 8 * dlc - 1) // 4
    chk["frame_bits_out_of_bounds"] = int(((fbits < lo) | (fbits > hi)).sum())
    chk["tx_duration_equals_bits"] = bool(((tx_end - tx_start) == fbits * BIT_NS).all())
    chk["tx_end_globally_nondecreasing"] = bool((np.diff(tx_end) >= 0).all())
    chk["tx_end_global_ties"] = int((np.diff(tx_end) == 0).sum())
    chk["capture_time_nondecreasing"] = bool((np.diff(capt) >= 0).all())
    # vehicle-wise observable order (per vehicle the bus serialises frames, so tx_end must strictly increase)
    vcodes, vinv = np.unique(veh, return_inverse=True)
    order = np.lexsort((np.arange(n), tx_end, vinv))            # stable observable order per vehicle
    chk["per_vehicle_row_order_is_time_order"] = bool((order == np.lexsort((np.arange(n), vinv))).all())
    vs_sorted = vinv[order]
    t_sorted = tx_end[order]
    same_v = vs_sorted[1:] == vs_sorted[:-1]
    chk["per_vehicle_timestamp_ties"] = int(((np.diff(t_sorted) == 0) & same_v).sum())
    # identical (vehicle, timestamp, id, dlc, payload) rows need a per-vehicle timestamp tie; count only those
    if chk["per_vehicle_timestamp_ties"] == 0:
        chk["duplicate_observation_rows"] = 0
    else:
        chk["duplicate_observation_rows"] = int(pd.DataFrame({"v": veh, "t": tx_end, "c": can_txt, "d": dlc, "p": pay_txt}).duplicated().sum())
    unmatched = pq.read_metadata(SRC / "raw" / sid / "unmatched_captures.parquet").num_rows
    chk["unmatched_captures"] = int(unmatched)

    # ------------------------------------------------------------------ labels alignment
    lb = pq.read_table(SRC / "ground_truth" / sid / "frame_labels.parquet")
    chk["label_schema_ok"] = lb.schema.names == LABEL_SCHEMA
    chk["labels_aligned"] = bool(lb.num_rows == n and lb.column("frame_uid").equals(fr.column("frame_uid")))
    flabel = _np(lb, "frame_label").astype(str)
    att_id = _np(lb, "attack_id").astype(str)
    fsrc = _np(lb, "frame_source").astype(str)
    mal = flabel == "malicious"
    chk["frame_label_values"] = ";".join(sorted(set(np.unique(flabel))))
    chk["malicious_without_attack_id"] = int((mal & (att_id == "None")).sum())
    chk["benign_with_attack_id"] = int((~mal & (att_id != "None")).sum())
    chk["benign_frame_source_not_generator"] = int((~mal & (fsrc != "benign_generator")).sum())
    chk["malicious_frame_source_values"] = ";".join(sorted(set(np.unique(fsrc[mal])))) if mal.any() else ""

    # ------------------------------------------------------------------ detector-safe observations
    obs = pa.Table.from_arrays([pa.array(np.arange(n, dtype=np.int64)), pa.array(tx_end, pa.int64()),
                                fr.column("vehicle_id"), fr.column("can_id"), pa.array(dlc.astype(np.int8)),
                                fr.column("payload")], schema=OBS_SCHEMA).combine_chunks()
    atomic_parquet(obs, out_obs_pq)
    atomic_csv(obs, out_obs_csv)
    chk["parquet_roundtrip_equal"] = bool(pq.read_table(out_obs_pq).equals(obs))
    chk["csv_roundtrip_equal"] = bool(read_csv_strict(out_obs_csv, OBS_SCHEMA).equals(obs))
    chk["observation_rows_written"] = n
    busx = pa.Table.from_arrays([pa.array(np.arange(n, dtype=np.int64)), pa.array(sim, pa.int64()),
                                 pa.array(tx_start, pa.int64()), pa.array(fbits, pa.int64()),
                                 pa.array(capt, pa.int64()), fr.column("channel")], schema=BUS_TIMING_SCHEMA)
    atomic_parquet(busx.combine_chunks(), md_dir / "frame_bus_timing.parquet")
    del busx

    # ------------------------------------------------------------------ ground truth (+ join keys)
    flab = pa.table({"frame_index": pa.array(np.arange(n, dtype=np.int64)),
                     **{c: lb.column(c) for c in LABEL_SCHEMA[2:]}}).combine_chunks()
    atomic_parquet(flab, gt_dir / "frame_labels.parquet")
    atomic_csv(flab, gt_dir / "frame_labels.csv.gz")
    del flab
    gt = {}
    for name in GT_TABLES:
        t = pq.read_table(SRC / "ground_truth" / sid / f"{name}.parquet")
        gt[name] = t
        extra = {}
        if name == "attack_frames":
            idx, bad = _index_of_uid(_np(t, "frame_uid"), sid)
            extra["frame_index"] = idx
            chk["attack_frames_foreign_uid"] = bad
        if name == "replay_map":
            ri, b1 = _index_of_uid(_np(t, "replay_frame_uid"), sid)
            oi, b2 = _index_of_uid(_np(t, "original_frame_uid"), sid)
            extra["replay_frame_index"], extra["original_frame_index"] = ri, oi
            chk["replay_map_foreign_uid"] = b1 + b2
        if extra:
            for k, v in extra.items():
                t = t.append_column(k, pa.array(v, pa.int64()))
        atomic_parquet(t, gt_dir / f"{name}.parquet")
        atomic_csv(t, gt_dir / (f"{name}.csv.gz" if name in FRAME_LEVEL_GT else f"{name}.csv"))
    att = gt["attacks"].to_pandas()
    camp = gt["campaigns"].to_pandas()
    memb = gt["campaign_membership"].to_pandas()
    drops = gt["dropped_frames"].to_pandas()
    for c in ("start_ns", "end_ns", "first_generated_ns", "last_generated_ns", "generated", "transmitted", "dropped",
              "queue_dropped", "expired_dropped", "captured", "skipped_inactive"):
        att[c] = pd.to_numeric(att[c].replace("", np.nan)).astype("Int64")

    # ------------------------------------------------------------------ metadata
    src_meta = SRC / "metadata" / sid
    (md_dir / "source").mkdir(parents=True, exist_ok=True)
    for f in ("metadata.json", "bus_stats.json"):
        shutil.copyfile(src_meta / f, md_dir / "source" / f)
    _copy_tree(src_meta / "config_snapshot", md_dir / "source" / "config_snapshot")
    shutil.copyfile(SRC / "validation" / sid / "validation_report.json", md_dir / "source" / "generator_validation_report.json")
    vs = pq.read_table(SRC / "raw" / sid / "vehicle_states.parquet", columns=["sim_time_ns", "vehicle_id", "speed_mps"]).to_pandas()
    vs["speed_mps"] = vs.speed_mps.astype(float)
    first_state = vs.groupby("vehicle_id").sim_time_ns.min().astype("int64").to_dict()
    speed = vs.groupby("vehicle_id").speed_mps.agg(["mean", "max"]).to_dict("index")
    vrows = []
    for v in sorted(fleet):
        a = route_asg.get(v, {})
        vrows.append({"vehicle_id": v, "profile": prof[v], "channel": fleet[v]["channel"],
                      "switch_on_ns": int(fleet[v]["switch_on_ns"]), "first_sumo_state_ns": int(first_state.get(v, -1)),
                      "ecu_activation_ns": int(first_state.get(v, 0)) + int(fleet[v]["switch_on_ns"]),
                      "route_loop": a.get("loop", ""), "route_start_edge": a.get("start_edge", ""),
                      "route_depart_pos_m": a.get("depart_pos_m", ""), "route_loop_repetitions": a.get("loop_repetitions", ""),
                      "mean_speed_mps": speed.get(v, {}).get("mean"), "max_speed_mps": speed.get(v, {}).get("max")})
    vdf = pd.DataFrame(vrows)
    atomic_csv(pa.Table.from_pandas(vdf, preserve_index=False), md_dir / "vehicles.csv")
    src_files = []
    for d, names in (("raw", ["can_frames", "unmatched_captures", "vehicle_states"]), ("ground_truth", ["frame_labels", *GT_TABLES])):
        for nm in names:
            p = SRC / d / sid / f"{nm}.parquet"
            src_files.append({"source_path": f"{d}/{sid}/{nm}.parquet", "bytes": p.stat().st_size})
    session_md = {
        "release_version": "fleetcan-sim-research-1.0", "source_dataset": "fleetcan-sim-scale-1.0", "session_id": sid,
        "configuration_id": rec["configuration_id"], "config_index": rec["config_index"], "repetition": rec["repetition"],
        "random_seed": rec["seed"], "phase": rec["phase"], "route_group": "G0" if rec["phase"] == "G0" else f"P{rec['phase']}",
        "purpose": rec["purpose"], "scenario": rec["scenario"], "attack_family": rec["attack"], "fleet_size": rec["N"],
        "campaign_size": rec["K"], "temporal_pattern": rec["temporal_pattern"], "independent_timing": rec["independent_timing"],
        "route_source": rec["route_source"], "member_selection": tags.get("member_selection"),
        "profile_composition": rec["composition"], "duration_s": rec["duration_s"],
        "generator_version": rec["generator_version"], "schema_version": rec["schema_version"],
        "generator_git_commit": rec["git_commit"], "matrix_id": rec["matrix_id"], "matrix_sha256": rec["matrix_sha256"],
        "sumo_version": meta["simulation"].get("sumo_version"), "route_seed": meta["simulation"]["route_generation"].get("route_seed"),
        "route_sha256": meta["simulation"]["route_generation"].get("route_sha256"),
        "source_files": src_files,
        "note": "Experimental metadata. Never a detector input (see documentation/DATA_DICTIONARY.md)."}
    atomic_json(session_md, md_dir / "session.json")

    # ------------------------------------------------------------------ per-vehicle pass (time order per vehicle)
    pay = payload_matrix(pay_txt, dlc)
    a_code = {a: i for i, a in enumerate(att.attack_id)}
    a_of_frame = np.full(n, -1, dtype=np.int64)
    if len(att):
        aid_series = pd.Series(att_id)
        a_of_frame = aid_series.map(a_code).fillna(-1).astype(np.int64).to_numpy()
    a_camp = dict(zip(att.attack_id, att.campaign_id))
    a_type = dict(zip(att.attack_id, att.attack_type))
    attacks_by_vehicle = att.groupby("vehicle_id").attack_id.apply(list).to_dict() if len(att) else {}
    win_attack: dict[str, dict] = {}
    desc_parts, lab_parts, veh_rows, canid_rows = [], [], [], []
    dstat = defaultdict(lambda: np.zeros((5, 24)))   # (profile, class) -> count,sum,sumsq,min,max
    timebins = defaultdict(int)
    hist = {"dlc": defaultdict(lambda: np.zeros(9, np.int64)), "byte": defaultdict(lambda: np.zeros((8, 256), np.int64)),
            "canid": defaultdict(lambda: defaultdict(int))}
    first_attack_start = int(att.start_ns.min()) if len(att) else None
    bus_ch = bus["channels"]
    for vi, v in enumerate(vcodes):
        rows = order[vinv[order] == vi]
        t = tx_end[rows]
        nv = len(rows)
        m = mal[rows]
        ids = can_int[rows]
        dl = dlc[rows]
        pv = pay[rows]
        p_ = prof[v]
        starts, feats, nanf = trace_descriptors(t, ids, dl, pv)
        nw = len(starts)
        cs = np.concatenate([[0], np.cumsum(m.astype(np.int64))])
        wmal = cs[starts + WINDOW] - cs[starts] if nw else np.zeros(0, np.int64)
        # first malicious frame time in each window
        mpos = np.flatnonzero(m)
        nxt_i = np.searchsorted(mpos, starts) if nw else np.zeros(0, np.int64)
        has = (nxt_i < mpos.size)
        first_mal = np.full(nw, -1, np.int64)
        okm = has & (wmal > 0)
        first_mal[okm] = t[mpos[nxt_i[okm]]]
        a_ids = [""] * nw
        c_ids = [""] * nw
        a_types = [""] * nw
        for a in attacks_by_vehicle.get(v, []):
            am = (a_of_frame[rows] == a_code[a]).astype(np.int64)
            ca = np.concatenate([[0], np.cumsum(am)])
            wa = ca[starts + WINDOW] - ca[starts] if nw else np.zeros(0, np.int64)
            for w in np.flatnonzero(wa > 0):
                a_ids[w] = f"{a_ids[w]};{a}" if a_ids[w] else a
                c = a_camp[a]
                if c not in ("None", "") and c not in c_ids[w].split(";"):
                    c_ids[w] = f"{c_ids[w]};{c}" if c_ids[w] else c
                ty = a_type[a]
                if ty not in a_types[w].split(";"):
                    a_types[w] = f"{a_types[w]};{ty}" if a_types[w] else ty
            win_attack[a] = {"vehicle_id": v, "counts": wa, "starts": starts, "t": t}
        wcls = np.where(wmal == 0, "benign_only", np.where(wmal == WINDOW, "malicious_only", "mixed"))
        if nw:
            desc_parts.append(pd.DataFrame({
                "vehicle_id": v, "window_index": np.arange(nw, dtype=np.int64),
                "first_frame_index": rows[starts].astype(np.int64), "last_frame_index": rows[starts + WINDOW - 1].astype(np.int64),
                "window_start_ns": t[starts], "window_end_ns": t[starts + WINDOW - 1],
                "descriptor_timestamp_ns": t[starts + WINDOW - 1], "nan_filled_features": nanf.astype(np.int64),
                **{c: feats[:, j] for j, c in enumerate(FEATURE_COLUMNS)}}))
            lab_parts.append(pd.DataFrame({
                "vehicle_id": v, "window_index": np.arange(nw, dtype=np.int64), "n_frames": WINDOW,
                "malicious_frame_count": wmal.astype(np.int64), "malicious_frame_ratio": wmal / WINDOW,
                "label_majority_original": (wmal * 2 >= WINDOW).astype(np.int8),
                "label_any_malicious": (wmal > 0).astype(np.int8), "window_class": wcls,
                "attack_ids": [x or "None" for x in a_ids], "campaign_ids": [x or "None" for x in c_ids],
                "attack_types": [x or "None" for x in a_types],
                "first_malicious_timestamp_ns": first_mal}))
            for cls in ("benign_only", "mixed", "malicious_only"):
                sel = wcls == cls
                if sel.any():
                    f = feats[sel]
                    acc = dstat[(p_, cls)]
                    if acc[0, 0] == 0:
                        acc[3], acc[4] = np.inf, -np.inf
                    acc[0] += sel.sum()
                    acc[1] += f.sum(axis=0)
                    acc[2] += (f * f).sum(axis=0)
                    acc[3] = np.minimum(acc[3], f.min(axis=0))
                    acc[4] = np.maximum(acc[4], f.max(axis=0))
        # ---- vehicle statistics
        b = ~m
        tb = t[b]
        idb = ids[b]
        uq, cnt = np.unique(idb, return_counts=True)
        iat = np.diff(t) / 1e6                                # ms
        iatb = np.diff(tb) / 1e6
        pvb = pv[b]
        with np.errstate(invalid="ignore"):
            bstd = np.nanstd(pvb, axis=0) if pvb.size else np.full(8, np.nan)
        qd_v = qd[rows]
        ch = fleet[v]["channel"]
        bc = bus_ch.get(ch, {})
        util_bins = np.asarray(bc.get("utilization_bins", []), dtype=float)
        dup_consec = 0
        pb_txt = pay_txt[rows][b]
        dlb = dl[b]
        for cid in uq:
            sel = idb == cid
            tt = tb[sel]
            per = np.diff(tt) / 1e6
            pp = pb_txt[sel]
            rep = int((pp[1:] == pp[:-1]).sum()) if pp.size > 1 else 0
            dup_consec += rep
            bs = pvb[sel]
            with np.errstate(invalid="ignore"):
                bst = np.nanstd(bs, axis=0)
            canid_rows.append({"session_id": sid, "vehicle_id": v, "profile": p_, "can_id": f"0x{cid:03X}",
                               "frames": int(sel.sum()), "dlc_values": ";".join(map(str, np.unique(dlb[sel]))),
                               "distinct_payloads": int(len(np.unique(pp))), "repeated_payload_frames": rep,
                               "mean_byte_std": float(np.nanmean(bst)) if np.isfinite(bst).any() else 0.0,
                               **_stats(per, "period_ms")})
            hist["canid"][p_][f"0x{cid:03X}"] += int(sel.sum())
        hist["dlc"][(p_, "benign")] += np.bincount(dl[b], minlength=9)[:9]
        if m.any():
            hist["dlc"][(p_, "malicious")] += np.bincount(dl[m], minlength=9)[:9]
        for i in range(8):
            col = pvb[:, i]
            col = col[np.isfinite(col)].astype(np.int64)
            hist["byte"][p_][i] += np.bincount(col, minlength=256)[:256]
        for sec, c in zip(*np.unique(tb // NS, return_counts=True)):
            timebins[(p_, int(sec))] += int(c)
        warm = tb[tb < first_attack_start] if first_attack_start is not None else tb
        veh_rows.append({
            "session_id": sid, "vehicle_id": v, "profile": p_, "attacked": v in attacks_by_vehicle,
            "n_attacks": len(attacks_by_vehicle.get(v, [])), "frames": nv, "benign_frames": int(b.sum()),
            "malicious_frames": int(m.sum()), "first_timestamp_ns": int(t[0]), "last_timestamp_ns": int(t[-1]),
            "active_duration_s": (int(t[-1]) - int(t[0])) / NS, "fps_over_session": nv / duration_s,
            "benign_fps_active": (b.sum() / ((tb[-1] - tb[0]) / NS)) if tb.size > 1 else None,
            "unique_can_ids": int(len(np.unique(ids))), "unique_benign_can_ids": int(len(uq)),
            "benign_can_id_entropy_bits": _entropy(cnt), "benign_most_common_id_ratio": float(cnt.max() / cnt.sum()) if cnt.size else None,
            **_stats(iat, "iat_ms"), **_stats(iatb, "benign_iat_ms"),
            "benign_mean_byte_std": float(np.nanmean(bstd)) if np.isfinite(bstd).any() else 0.0,
            "benign_constant_byte_positions": int(np.sum(bstd == 0)),
            "benign_distinct_payloads": int(len(np.unique(pb_txt))),
            "benign_repeated_payload_frames": dup_consec,
            "benign_dlc_mean": float(dl[b].mean()) if b.any() else None,
            "warmup_benign_frames": int(warm.size),
            "queue_delay_us_mean": float(qd_v.mean() / 1e3), "queue_delay_us_max": float(qd_v.max() / 1e3),
            "queued_frame_fraction": float((qd_v > 0).mean()),
            "benign_queue_delay_us_mean": float(qd_v[b].mean() / 1e3) if b.any() else None,
            "benign_queue_delay_us_max": float(qd_v[b].max() / 1e3) if b.any() else None,
            "bus_utilization_overall": bc.get("utilization_overall"),
            "bus_utilization_max_bin": float(util_bins.max()) if util_bins.size else None,
            "bus_transmitted": bc.get("transmitted"), "bus_dropped": bc.get("dropped"),
            "bus_queue_drops": bc.get("queue_drops"), "bus_expired_drops": bc.get("expired_drops"),
            "windows": nw, "windows_with_malicious": int((wmal > 0).sum()),
            "windows_majority_malicious": int((wmal * 2 >= WINDOW).sum()),
            "windows_all_malicious": int((wmal == WINDOW).sum()),
            "benign_window_span_s_median": float(np.median((t[starts[wmal == 0] + WINDOW - 1] - t[starts[wmal == 0]]) / NS)) if (wmal == 0).any() else None,
            "timestamp_order_violations": int((np.diff(t) <= 0).sum()),
            "switch_on_ns": int(fleet[v]["switch_on_ns"]), "route_loop": route_asg.get(v, {}).get("loop", ""),
            "mean_speed_mps": speed.get(v, {}).get("mean")})
    desc = pd.concat(desc_parts, ignore_index=True)
    labs = pd.concat(lab_parts, ignore_index=True)
    dt = pa.Table.from_pandas(desc, preserve_index=False)
    atomic_parquet(dt, de_dir / "descriptors.parquet")
    atomic_csv(dt, de_dir / "descriptors.csv.gz")
    lt = pa.Table.from_pandas(labs, preserve_index=False)
    atomic_parquet(lt, gt_dir / "window_labels.parquet")
    atomic_csv(lt, gt_dir / "window_labels.csv.gz")
    chk["descriptor_windows"] = len(desc)
    chk["expected_windows"] = int(sum(((x["frames"] - WINDOW) // STRIDE + 1) if x["frames"] >= WINDOW else 0 for x in veh_rows))
    chk["descriptor_window_count_ok"] = chk["descriptor_windows"] == chk["expected_windows"]
    chk["descriptor_label_rows_match"] = bool(len(labs) == len(desc) and (labs.window_index.values == desc.window_index.values).all())
    chk["descriptor_timestamp_is_last_frame"] = bool((desc.descriptor_timestamp_ns.values == tx_end[desc.last_frame_index.values]).all())
    chk["descriptor_window_monotone"] = bool(desc.groupby("vehicle_id").descriptor_timestamp_ns.apply(lambda s: (np.diff(s.values) > 0).all()).all())

    # ------------------------------------------------------------------ attacks (configured vs observable)
    win_s = sim
    a_rows = []
    outside_total = 0
    for r in att.itertuples():
        sel = a_of_frame == a_code[r.attack_id]
        idx = np.flatnonzero(sel)
        cnt_ = int(idx.size)
        v = r.vehicle_id
        start, end = int(r.start_ns), int(r.end_ns)
        rec_a = {"session_id": sid, "attack_id": r.attack_id, "attack_family": r.attack_type, "vehicle_id": v,
                 "profile": prof[v], "campaign_id": r.campaign_id, "configured_start_ns": start, "configured_end_ns": end,
                 "configured_duration_s": (end - start) / NS,
                 "requested_rate_hz": float(r.requested_rate_hz) if r.requested_rate_hz not in ("", None) else None,
                 "generated": int(r.generated), "transmitted": int(r.transmitted), "dropped": int(r.dropped),
                 "queue_dropped": int(r.queue_dropped), "expired_dropped": int(r.expired_dropped), "captured": int(r.captured),
                 "skipped_inactive": int(r.skipped_inactive), "source_first_generated_ns": int(r.first_generated_ns) if pd.notna(r.first_generated_ns) else None,
                 "derived_malicious_frames": cnt_}
        if cnt_:
            outside = int(((win_s[idx] < start) | (win_s[idx] > end)).sum()) + int((veh[idx] != v).sum())
            outside_total += outside
            fi, la = int(idx[0]), int(idx[-1])
            ti = tx_end[idx]
            ids_a = can_int[idx]
            rec_a.update({
                "derived_first_malicious_frame_index": fi, "derived_last_malicious_frame_index": la,
                "derived_first_malicious_timestamp_ns": int(ti.min()), "derived_last_malicious_timestamp_ns": int(ti.max()),
                "derived_first_malicious_generation_ns": int(sim[idx].min()),
                "derived_onset_delay_ms": (int(ti.min()) - start) / 1e6, "derived_end_delay_ms": (int(ti.max()) - end) / 1e6,
                "derived_observable_duration_s": (int(ti.max()) - int(ti.min())) / NS,
                "derived_injection_rate_hz": cnt_ / ((end - start) / NS),
                "derived_unique_can_ids": int(len(np.unique(ids_a))), "derived_dlc_values": int(len(np.unique(dlc[idx]))),
                "derived_distinct_payloads": int(len(np.unique(pay_txt[idx]))),
                "derived_malicious_queue_delay_us_mean": float(qd[idx].mean() / 1e3),
                "derived_malicious_queue_delay_us_max": float(qd[idx].max() / 1e3),
                "frames_outside_configured_interval": outside})
            vb = (veh == v) & ~mal
            during = vb & (tx_end >= start) & (tx_end <= end)
            before = vb & (tx_end >= start - (end - start)) & (tx_end < start)
            rec_a["derived_benign_fps_during_attack"] = during.sum() / ((end - start) / NS)
            rec_a["derived_benign_fps_before_attack"] = before.sum() / ((end - start) / NS)
            rec_a["derived_benign_queue_delay_us_during_attack"] = float(qd[during].mean() / 1e3) if during.any() else None
            ub = np.asarray(bus_ch.get(fleet[v]["channel"], {}).get("utilization_bins", []), dtype=float)
            b0, b1 = start // bus["bin_ns"], end // bus["bin_ns"]
            rec_a["derived_bus_utilization_during_attack"] = float(ub[b0:b1].mean()) if ub.size > b0 else None
            wa = win_attack.get(r.attack_id)
            if wa is not None:
                c, st_, tt = wa["counts"], wa["starts"], wa["t"]
                rec_a["derived_windows_with_attack_frames"] = int((c > 0).sum())
                rec_a["derived_majority_windows"] = int((c * 2 >= WINDOW).sum())
                rec_a["derived_max_attack_frames_in_window"] = int(c.max()) if c.size else 0
                for k in KS:
                    w = np.flatnonzero(c >= k)
                    rec_a[f"derived_first_window_ge{k}_index"] = int(w[0]) if w.size else None
                    rec_a[f"derived_first_window_ge{k}_end_ns"] = int(tt[st_[w[0]] + WINDOW - 1]) if w.size else None
                    rec_a[f"derived_first_window_ge{k}_delay_ms"] = (int(tt[st_[w[0]] + WINDOW - 1]) - start) / 1e6 if w.size else None
                w = np.flatnonzero(c > 0)
                rec_a["derived_last_window_index"] = int(w[-1]) if w.size else None
                rec_a["derived_last_window_end_ns"] = int(tt[st_[w[-1]] + WINDOW - 1]) if w.size else None
        a_rows.append(rec_a)
    adf = pd.DataFrame(a_rows)
    chk["attacks"] = len(att)
    chk["malicious_frames"] = int(mal.sum())
    chk["malicious_outside_configured_interval_or_vehicle"] = outside_total
    chk["malicious_on_non_attacked_vehicle"] = int(sum(x["malicious_frames"] for x in veh_rows if not x["attacked"]))
    chk["attack_captured_equals_labelled"] = bool(len(adf) == 0 or (adf.captured == adf.derived_malicious_frames).all())
    chk["attack_generated_equals_transmitted_plus_dropped"] = bool(len(adf) == 0 or (adf.generated == adf.transmitted + adf.dropped).all())
    chk["dropped_rows_equal_bus_drops"] = bool(len(drops) == sum(int(c.get("dropped", 0)) for c in bus_ch.values()))
    chk["dropped_frames_rows"] = len(drops)
    fam = set(att.attack_type) if len(att) else set()
    chk["attack_family_matches_config"] = (fam <= {rec["attack"]}) if rec["attack"] != "none" else not fam
    chk["unlabelled_attack_frames"] = int(mal.sum() - (adf.derived_malicious_frames.sum() if len(adf) else 0))
    if len(adf):
        atomic_parquet(pa.Table.from_pandas(adf, preserve_index=False), gt_dir / "attack_instances.parquet")
        atomic_csv(pa.Table.from_pandas(adf, preserve_index=False), gt_dir / "attack_instances.csv")

    # ------------------------------------------------------------------ campaigns
    c_rows = []
    by_a = adf.set_index("attack_id") if len(adf) else pd.DataFrame()
    mem_ok = True
    for c in camp.itertuples():
        mm = memb[memb.campaign_id == c.campaign_id].copy()
        mm["s"] = mm.vehicle_attack_start_ns.astype(np.int64)
        targets = sorted(c.target_vehicles.split(";")) if c.target_vehicles else []
        mem_ok &= sorted(mm.vehicle_id) == targets and len(mm) == int(c.vehicle_count)
        mem_ok &= set(mm.attack_id) <= set(att.attack_id)
        s = mm.s.to_numpy()

        def col(name):
            if name not in by_a.columns:
                return np.full(len(mm), np.nan)
            return np.array([by_a.loc[a, name] if (a in by_a.index and pd.notna(by_a.loc[a, name])) else np.nan for a in mm.attack_id], float)
        f_obs = col("derived_first_malicious_timestamp_ns")
        f_w1 = col("derived_first_window_ge1_end_ns")
        f_maj = col("derived_first_window_ge50_end_ns")
        cfg_gap = np.diff(np.sort(s))

        def order_agree(obs):
            ok = np.isfinite(obs)
            pairs = agree = 0
            for i in range(len(s)):
                for j in range(i + 1, len(s)):
                    if s[i] != s[j] and ok[i] and ok[j]:
                        pairs += 1
                        agree += (np.sign(s[j] - s[i]) == np.sign(obs[j] - obs[i]))
            return pairs, agree

        p1, a1 = order_agree(f_obs)
        p2, a2 = order_agree(f_w1)
        spread = lambda x: (np.nanmax(x) - np.nanmin(x)) / NS if np.isfinite(x).any() else None     # noqa: E731
        c_rows.append({
            "session_id": sid, "campaign_id": c.campaign_id, "coordinated": str(c.coordinated).lower(),
            "attack_family": c.attack_type, "delay_pattern": c.delay_pattern, "campaign_size": int(c.vehicle_count),
            "members": len(mm), "member_profiles": "".join(sorted(prof[v] for v in mm.vehicle_id)),
            "configured_start_ns": int(c.campaign_start_ns), "configured_end_ns": int(c.campaign_end_ns),
            "configured_duration_s": float(c.campaign_duration_s), "configured_onset_spread_s": (s.max() - s.min()) / NS,
            "configured_min_positive_gap_s": float(cfg_gap[cfg_gap > 0].min() / NS) if (cfg_gap > 0).any() else 0.0,
            "common_overlap_s": float(c.common_overlap_s) if c.common_overlap_s not in ("", None) else None,
            "temporal_pattern": rec["temporal_pattern"], "scenario": rec["scenario"],
            "members_without_observable_frames": int((~np.isfinite(f_obs)).sum()),
            "members_without_attack_window": int((~np.isfinite(f_w1)).sum()),
            "members_without_majority_window": int((~np.isfinite(f_maj)).sum()),
            "derived_observable_onset_spread_s": spread(f_obs), "derived_window_onset_spread_s": spread(f_w1),
            "derived_majority_window_onset_spread_s": spread(f_maj),
            "derived_max_onset_lag_ms": float(np.nanmax(f_obs - s) / 1e6) if np.isfinite(f_obs).any() else None,
            "derived_max_window_lag_ms": float(np.nanmax(f_w1 - s) / 1e6) if np.isfinite(f_w1).any() else None,
            "ordered_pairs": p1, "frame_order_agreeing_pairs": int(a1), "window_order_pairs": p2,
            "window_order_agreeing_pairs": int(a2)})
    cdf = pd.DataFrame(c_rows)
    chk["campaigns"] = len(camp)
    chk["coordinated_campaigns"] = int((cdf.coordinated == "true").sum()) if len(cdf) else 0
    chk["membership_consistent"] = bool(mem_ok)
    chk["every_attack_in_a_campaign_table"] = bool(len(att) == 0 or set(att.campaign_id) <= set(camp.campaign_id) | {"None"})

    # ------------------------------------------------------------------ replay
    r_rows = []
    if (att.attack_type == "replay").any():
        rm = gt["replay_map"].to_pandas()
        ri = rm.replay_frame_uid.str.rsplit("-F", n=1).str[1].astype(np.int64).to_numpy()
        oi = rm.original_frame_uid.str.rsplit("-F", n=1).str[1].astype(np.int64).to_numpy()
        for r in att[att.attack_type == "replay"].itertuples():
            p = json.loads(r.params)
            if "window_ns" in p:                                   # same rule as src/attack_engine.py Replay.setup
                w0, w1 = int(p["window_ns"][0]), int(p["window_ns"][1])
            else:
                w0, w1 = round(float(p["window_s"][0]) * 1e9), round(float(p["window_s"][1]) * 1e9)
            off = round(float(p.get("time_offset_ms", 0.0)) * 1e6)
            srcv = p.get("source_vehicle", r.vehicle_id)
            start = int(r.start_ns)
            sel = (rm.attack_id == r.attack_id).to_numpy()
            o, q = oi[sel], ri[sel]
            elig = np.flatnonzero((veh == srcv) & (fsrc == "benign_generator") & (tx_end >= w0) & (tx_end < w1))
            first_elig = int(tx_end[elig[0]]) if elig.size else None
            pred_first = start + (first_elig - w0) + off if first_elig is not None else None
            amask = np.flatnonzero(a_of_frame == a_code[r.attack_id])
            act = int(first_state.get(srcv, 0)) + int(fleet[srcv]["switch_on_ns"])
            first_src_frame = int(tx_end[np.flatnonzero(veh == srcv)[0]])
            links_ok = bool(o.size and (can_txt[o] == can_txt[q]).all() and (pay_txt[o] == pay_txt[q]).all()
                            and (veh[o] == srcv).all() and (veh[q] == r.vehicle_id).all())
            shift_ok = bool(o.size and ((sim[q] - tx_end[o]) == (start - w0 + off)).all())
            orig_benign = bool(o.size and (~mal[o]).all())
            q_mal = bool(q.size and mal[q].all() and (a_of_frame[q] == a_code[r.attack_id]).all())
            set_eq = bool(np.array_equal(np.sort(o), elig))
            first_obs = int(tx_end[amask].min()) if amask.size else None
            onset_ms = (first_obs - start) / 1e6 if first_obs is not None else None
            if not (links_ok and shift_ok and orig_benign and q_mal and set_eq):
                cls = "genuine_labeling_defect" if not (q_mal and orig_benign) else "unresolved_inconsistency"
            elif pred_first is not None and int(r.first_generated_ns) == pred_first:
                cls = "source_frame_availability_delay" if act > w0 else "expected_replay_behavior"
            else:
                cls = "unresolved_inconsistency"
            r_rows.append({
                "session_id": sid, "attack_id": r.attack_id, "campaign_id": r.campaign_id, "vehicle_id": r.vehicle_id,
                "source_vehicle": srcv, "profile": prof[r.vehicle_id], "temporal_pattern": rec["temporal_pattern"],
                "configured_start_ns": start, "configured_end_ns": int(r.end_ns), "replay_window_start_ns": w0,
                "replay_window_end_ns": w1, "time_offset_ns": off,
                "source_first_sumo_state_ns": int(first_state.get(srcv, -1)), "source_switch_on_ns": int(fleet[srcv]["switch_on_ns"]),
                "source_ecu_activation_ns": act, "source_first_frame_ns": first_src_frame,
                "ecu_active_at_window_start": act <= w0,
                "first_eligible_source_frame_index": int(elig[0]) if elig.size else None,
                "first_eligible_source_frame_ns": first_elig, "eligible_source_frames": int(elig.size),
                "predicted_first_injection_ns": pred_first, "source_first_generated_ns": int(r.first_generated_ns),
                "first_injected_generation_ns": int(sim[amask].min()) if amask.size else None,
                "first_observable_replay_frame_index": int(amask[0]) if amask.size else None,
                "first_observable_replay_frame_ns": first_obs, "configured_to_observable_onset_ms": onset_ms,
                "availability_delay_ms": (first_elig - w0) / 1e6 if first_elig is not None else None,
                "bus_delay_ms": (first_obs - int(sim[amask].min())) / 1e6 if amask.size else None,
                "replay_links": int(o.size), "links_same_id_payload_vehicle": links_ok, "links_time_shift_exact": shift_ok,
                "originals_benign": orig_benign, "replayed_frames_labelled_malicious": q_mal,
                "links_equal_eligible_source_set": set_eq, "originals_before_start": int((tx_end[o] < start).sum()),
                "classification": cls})
    rdf = pd.DataFrame(r_rows)

    # ------------------------------------------------------------------ write per-session work tables
    for name, df in (("vehicles", pd.DataFrame(veh_rows)), ("attacks", adf), ("campaigns", cdf), ("replay", rdf),
                     ("canid", pd.DataFrame(canid_rows))):
        if len(df):
            atomic_parquet(pa.Table.from_pandas(df, preserve_index=False), wk / f"{name}.parquet")
    tb_df = pd.DataFrame([{"session_id": sid, "profile": k[0], "second": k[1], "benign_frames": v} for k, v in sorted(timebins.items())])
    atomic_parquet(pa.Table.from_pandas(tb_df, preserve_index=False), wk / "timebins.parquet")
    ds = []
    for (p_, cls), acc in sorted(dstat.items()):
        for j, f in enumerate(FEATURE_COLUMNS):
            ds.append({"session_id": sid, "profile": p_, "window_class": cls, "feature": f, "n": int(acc[0, j]),
                       "sum": acc[1, j], "sumsq": acc[2, j], "min": acc[3, j], "max": acc[4, j]})
    atomic_parquet(pa.Table.from_pandas(pd.DataFrame(ds), preserve_index=False), wk / "desc_stats.parquet")
    atomic_json({"dlc": {f"{k[0]}|{k[1]}": v for k, v in hist["dlc"].items()},
                 "byte": {k: v for k, v in hist["byte"].items()},
                 "canid": {k: dict(v) for k, v in hist["canid"].items()}}, wk / "hist.json")
    files = {}
    for p in sorted(list((RELEASE / "data").glob(f"*/{sid}/**/*"))):
        if p.is_file():
            files[str(p.relative_to(RELEASE))] = p.stat().st_size
    summary = {"session_id": sid, "record": rec, "checks": chk, "frames": n, "malicious_frames": int(mal.sum()),
               "descriptor_windows": len(desc), "attacks": len(att), "campaigns": len(camp),
               "output_files": files, "seconds": round(time.time() - t_start, 2)}
    atomic_json(summary, wk / "summary.json")
    (wk / "DONE").write_text("ok\n")
    return summary
