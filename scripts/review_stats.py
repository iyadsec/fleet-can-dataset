"""Independent scientific review of fleetcan-sim-scale-1.0: per-session descriptive statistics (READ-ONLY).

For every one of the 835 frozen sessions, reads raw/<sid>/can_frames, ground_truth/<sid>/*, metadata/<sid>/* and
computes descriptive statistics and consistency checks. No detector, no model, nothing written under data/.
Output: <OUT>/work/session_stats.jsonl (one JSON object per session) and <OUT>/work/profile_parts.jsonl.

Usage: .venv/bin/python scripts/review_stats.py --out DIR [--workers 3] [--sessions SID ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
NS = 1_000_000_000
HEXV = np.full(256, 255, dtype=np.uint8)
for i, ch in enumerate("0123456789ABCDEF"):
    HEXV[ord(ch)] = i
    HEXV[ord(ch.lower())] = i


def payload_bytes(payload: pd.Series) -> np.ndarray:
    """(n, 8) uint8 bytes; positions >= DLC are 0 (FLEET-GUARD convention: missing payload positions -> 0)."""
    s = payload.fillna("").str.ljust(16, "0").str.slice(0, 16)
    raw = np.frombuffer("".join(s.tolist()).encode("ascii"), dtype=np.uint8).reshape(-1, 16)
    v = HEXV[raw]
    if (v == 255).any():
        raise ValueError("non-hex payload character")
    return (v[:, 0::2] * 16 + v[:, 1::2]).astype(np.uint8)


def bits_bounds(dlc: np.ndarray):
    """Classical CAN 2.0A data frame incl. 3-bit IFS: 47 + 8*DLC bits without stuffing; worst-case stuff bits
    floor((34 + 8*DLC - 1) / 4) over the stuffable region (SOF..CRC)."""
    lo = 47 + 8 * dlc
    return lo, lo + (34 + 8 * dlc - 1) // 4


def fnum(x):
    return float(x) if x not in ("", None, "None") else None


def rd(sid, d, t, cols=None):
    return pq.read_table(DATA / d / sid / f"{t}.parquet", columns=cols).to_pandas()


def session(sid: str) -> tuple[dict, list]:
    meta = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
    tags = meta["tags"]
    bus = json.loads((DATA / "metadata" / sid / "bus_stats.json").read_text())
    prof = {v["vehicle_id"]: v["profile"][-1] for v in meta["fleet"]["vehicles"]}
    duration = float(meta["simulation"]["duration_s_configured"])
    fr = rd(sid, "raw", "can_frames", ["frame_uid", "vehicle_id", "can_id", "dlc", "payload", "frame_bits", "tx_end_ns", "sim_time_ns"])
    lb = rd(sid, "ground_truth", "frame_labels", ["frame_uid", "frame_label", "attack_type", "attack_id", "frame_source"])
    att = rd(sid, "ground_truth", "attacks")
    mem = rd(sid, "ground_truth", "campaign_membership")
    camp = rd(sid, "ground_truth", "campaigns")
    drops = rd(sid, "ground_truth", "dropped_frames", ["drop_kind", "frame_label"])
    out = {"session_id": sid, "config_id": tags["config_id"], "repetition": tags["repetition"], "seed": tags["seed"], "phase": tags["phase"],
           "N": tags["N"], "K": tags["K"], "scenario": tags["scenario"], "attack": tags["attack"], "timing": tags["temporal_pattern"],
           "independent_timing": tags["independent_timing"], "duration_s": duration, "frames": len(fr),
           "N_A": tags["N_A"], "N_B": tags["N_B"], "N_C": tags["N_C"], "K_A": tags["K_A"], "K_B": tags["K_B"], "K_C": tags["K_C"]}
    chk = {}
    # ---------- J: label alignment and A: structure
    chk["labels_aligned"] = bool(len(lb) == len(fr) and (lb.frame_uid.values == fr.frame_uid.values).all())
    mal = (lb.frame_label.values == "malicious")
    canid = fr.can_id.map(lambda x: int(x, 16)).to_numpy()
    dlc = fr.dlc.to_numpy()
    plen = fr.payload.fillna("").str.len().to_numpy()
    lo, hi = bits_bounds(dlc)
    fb = fr.frame_bits.to_numpy()
    chk["id_11bit_violations"] = int((canid > 0x7FF).sum() + (canid < 0).sum())
    chk["dlc_range_violations"] = int(((dlc < 0) | (dlc > 8)).sum())
    chk["payload_length_violations"] = int((plen != 2 * dlc).sum())
    chk["frame_bits_violations"] = int(((fb < lo) | (fb > hi)).sum())
    chk["frame_uid_unique"] = bool(fr.frame_uid.is_unique)
    pb = payload_bytes(fr.payload)
    veh = fr.vehicle_id.to_numpy()
    t = fr.tx_end_ns.to_numpy()
    # ---------- per vehicle
    attacked = set(att.vehicle_id)
    pv = []
    vstats = {}
    d = pd.DataFrame({"v": veh, "id": canid, "t": t, "mal": mal, "dlc": dlc})
    for v, g in d.groupby("v", sort=True):
        n, nm = len(g), int(g.mal.sum())
        tt = g.t.to_numpy()
        win = (n - 100) // 50 + 1 if n >= 100 else 0
        m = g.mal.to_numpy().astype(np.int64)
        if win:
            cs = np.concatenate([[0], np.cumsum(m)])
            starts = np.arange(win) * 50
            wm = cs[starts + 100] - cs[starts]
            wdur = (tt[starts + 99] - tt[starts]) / NS
            w_any, w_all = int((wm > 0).sum()), int((wm == 100).sum())
            wben = wdur[wm == 0]
            wmalf = wdur[wm > 0]
        else:
            w_any = w_all = 0
            wben = wmalf = np.array([])
        vstats[v] = {"vehicle_id": v, "profile": prof[v], "attacked": v in attacked, "frames": n, "malicious": nm,
                     "benign_rate_fps": (n - nm) / duration, "windows": int(win), "windows_with_malicious": w_any,
                     "windows_all_malicious": w_all, "benign_window_s_median": float(np.median(wben)) if len(wben) else None,
                     "malicious_window_s_median": float(np.median(wmalf)) if len(wmalf) else None}
    out["vehicles"] = len(vstats)
    out["vehicles_with_malicious_frames"] = sum(1 for x in vstats.values() if x["malicious"])
    out["benign_rate_fps_mean"] = float(np.mean([x["benign_rate_fps"] for x in vstats.values()]))
    out["windows"] = sum(x["windows"] for x in vstats.values())
    out["windows_with_malicious"] = sum(x["windows_with_malicious"] for x in vstats.values())
    out["windows_all_malicious"] = sum(x["windows_all_malicious"] for x in vstats.values())
    bw = [x["benign_window_s_median"] for x in vstats.values() if x["benign_window_s_median"] is not None]
    mw = [x["malicious_window_s_median"] for x in vstats.values() if x["malicious_window_s_median"] is not None]
    out["benign_window_s_median"] = float(np.median(bw)) if bw else None
    out["malicious_window_s_median"] = float(np.median(mw)) if mw else None
    # ---------- J: malicious frames only on attacked vehicles and inside attack windows
    chk["malicious_on_non_attacked_vehicle"] = int(sum(x["malicious"] for x in vstats.values() if not x["attacked"]))
    win_by_attack = {r.attack_id: (int(r.start_ns), int(r.end_ns), r.vehicle_id) for r in att.itertuples()}
    am = lb.attack_id.values
    outside = 0
    onset = {}
    last = {}
    for aid, (s0, e0, v) in win_by_attack.items():
        sel = (am == aid)
        if not sel.any():
            continue
        st = fr.sim_time_ns.to_numpy()[sel]
        outside += int(((st < s0) | (st > e0 + 1_000_000)).sum())     # generation time inside [start, end (+1 ms replay offset)]
        if (veh[sel] != v).any():
            outside += int((veh[sel] != v).sum())
        onset[aid] = int(t[sel].min()) - s0                              # observable: first malicious tx_end - configured start
        last[aid] = int(t[sel].max()) - e0
    chk["malicious_outside_attack_window"] = outside
    chk["benign_frames_with_attack_id"] = int(((~mal) & (pd.Series(am).fillna("None").astype(str).values != "None")).sum())
    chk["attack_type_matches_session"] = bool(set(lb.attack_type[mal].unique()) <= ({tags["attack"]} if tags["attack"] != "none" else set()))
    out["attacks"] = len(att)
    out["onset_delay_ms"] = [round(x / 1e6, 4) for x in onset.values()]
    out["end_delay_ms"] = [round(x / 1e6, 4) for x in last.values()]
    # ---------- attacks: rate, ids, drops
    a_rows = []
    for r in att.itertuples():
        sel = (am == r.attack_id)
        ids = canid[sel]
        a_rows.append({"session_id": sid, "attack_id": r.attack_id, "attack_type": r.attack_type, "vehicle_id": r.vehicle_id,
                       "profile": prof[r.vehicle_id], "campaign_id": r.campaign_id, "configured_duration_s": (int(r.end_ns) - int(r.start_ns)) / NS,
                       "requested_rate_hz": fnum(r.requested_rate_hz), "observed_rate_hz": fnum(r.observed_rate_hz),
                       "generated": int(r.generated), "transmitted": int(r.transmitted), "queue_dropped": int(r.queue_dropped),
                       "expired_dropped": int(r.expired_dropped), "captured": int(r.captured), "malicious_frames_in_raw": int(sel.sum()),
                       "unique_can_ids": int(len(np.unique(ids))), "dlc_values": int(len(np.unique(dlc[sel]))),
                       "onset_delay_ms": round(onset.get(r.attack_id, np.nan) / 1e6, 4) if r.attack_id in onset else None})
    out["attack_rows"] = a_rows
    # malfunction semantic value
    if tags["attack"] == "malfunction":
        af = rd(sid, "ground_truth", "attack_frames", ["message_name", "signal", "malicious_value"])
        out["malfunction_value_250_fraction"] = float((af.malicious_value.astype(float) == 250.0).mean())
        out["malfunction_messages"] = sorted(af.message_name.unique().tolist())
    # ---------- G: replay linkage
    if tags["attack"] == "replay":
        rm = rd(sid, "ground_truth", "replay_map")
        f_idx = pd.Series(np.arange(len(fr)), index=fr.frame_uid.values)
        oi = f_idx.reindex(rm.original_frame_uid.values).to_numpy()
        ri = f_idx.reindex(rm.replay_frame_uid.values).to_numpy()
        ok_exist = ~np.isnan(oi) & ~np.isnan(ri)
        oi, ri = oi[ok_exist].astype(int), ri[ok_exist].astype(int)
        same = (canid[oi] == canid[ri]) & (fr.payload.values[oi] == fr.payload.values[ri]) & (veh[oi] == veh[ri])
        gen = fr.sim_time_ns.to_numpy()
        shift = gen[ri] - t[oi]                                           # replay generation time - original tx_end
        # replay generation time = start + (original tx_end - w0) + 0.25 ms with w0 = start - T, so
        # (replay generation time - original tx_end) = T + 0.25 ms = configured end - start of the replay attack
        st_by = {r.attack_id: int(r.start_ns) for r in att.itertuples()}
        span_by = {r.attack_id: int(r.end_ns) - int(r.start_ns) for r in att.itertuples()}
        rm2 = rm[ok_exist]
        dev = np.abs(shift - np.array([span_by[x] for x in rm2.attack_id], dtype=np.int64))
        out["replay"] = {"links": int(len(rm)), "missing_frames": int((~ok_exist).sum()), "same_id_payload_bus": int(same.sum()),
                         "shift_max_dev_ns": int(dev.max()) if len(dev) else None,
                         "originals_before_start": int((t[oi] < np.array([st_by[a] for a in rm2.attack_id])).sum()),
                         "attacks_with_links": int(rm.attack_id.nunique())}
    # ---------- H/I: campaigns and configured timing
    cps = []
    for c in camp.itertuples():
        m = mem[mem.campaign_id == c.campaign_id].sort_values("vehicle_attack_start_ns")
        rel = (m.vehicle_attack_start_ns.astype(np.int64) - int(c.campaign_start_ns)).tolist()
        obs = [onset.get(a) for a in m.attack_id]
        obs_rel = [int(m.vehicle_attack_start_ns.iloc[i]) + o - int(c.campaign_start_ns) if o is not None else None
                   for i, o in enumerate(obs)]
        cps.append({"campaign_id": c.campaign_id, "coordinated": str(c.coordinated), "attack_type": c.attack_type, "members": int(len(m)),
                    "configured_spread_s": (max(rel) - min(rel)) / NS if rel else 0.0, "configured_delays_s": [x / NS for x in rel],
                    "observed_first_frame_rel_s": [round(x / NS, 6) if x is not None else None for x in obs_rel],
                    "member_profiles": "".join(sorted(prof[v] for v in m.vehicle_id))})
    out["campaigns"] = cps
    out["coordinated_campaigns"] = sum(c["coordinated"] == "true" for c in cps)
    out["independent_campaigns"] = sum(c["coordinated"] == "false" for c in cps)
    # ---------- bus
    chs = bus["channels"].values()
    out["bus_utilization_mean"] = float(np.mean([c["utilization_overall"] for c in chs]))
    out["bus_utilization_max"] = float(max(c["utilization_overall"] for c in chs))
    out["bus_utilization_peak_bin"] = float(max(max(c["utilization_bins"]) for c in chs))
    out["queue_drops"] = int(sum(c["queue_drops"] for c in chs))
    out["expired_drops"] = int(sum(c["expired_drops"] for c in chs))
    out["dropped_frames_rows"] = int(len(drops))
    out["dropped_benign"] = int((drops.frame_label == "benign").sum()) if len(drops) else 0
    aw = bus.get("attack_windows") or []
    out["attack_window_util"] = [{"baseline": w["baseline"]["utilization"], "during": w["during"]["utilization"],
                                  "benign_dropped_during": w["during"]["benign_dropped"],
                                  "benign_queue_delay_p99_us_during": w["during"]["benign_queue_delay_us"]["p99"]} for w in aw]
    # ---------- payload / dlc / id distributions (benign vs malicious)
    out["dlc_benign"] = dict(Counter(dlc[~mal].tolist()))
    out["dlc_malicious"] = dict(Counter(dlc[mal].tolist()))
    out["unique_ids_benign"] = int(len(np.unique(canid[~mal])))
    out["unique_ids_malicious"] = int(len(np.unique(canid[mal]))) if mal.any() else 0
    out["byte_mean_benign"] = pb[~mal].mean(axis=0).round(3).tolist()
    out["byte_mean_malicious"] = pb[mal].mean(axis=0).round(3).tolist() if mal.any() else None
    out["zero_byte_fraction_benign"] = float((pb[~mal] == 0).mean())
    out["zero_byte_fraction_malicious"] = float((pb[mal] == 0).mean()) if mal.any() else None
    # ---------- inter-arrival (benign, per vehicle and CAN ID, observable tx_end)
    b = d[~mal].copy()
    b["dt"] = b.groupby(["v", "id"]).t.diff()
    per = b.dropna().groupby(["v", "id"]).dt.agg(["median", "std", "count"])
    per = per[per["count"] >= 5]
    out["benign_period_jitter_cv_median"] = float((per["std"] / per["median"]).median()) if len(per) else None
    out["benign_period_jitter_cv_p99"] = float((per["std"] / per["median"]).quantile(0.99)) if len(per) else None
    out["checks"] = chk
    # ---------- profile parts (benign only; aggregated across sessions later)
    parts = []
    for p in "ABC":
        vs = [v for v in vstats if prof[v] == p]
        if not vs:
            continue
        sel = (~mal) & np.isin(veh, vs)
        ids, cnt = np.unique(canid[sel], return_counts=True)
        pp = per.reset_index()
        pp = pp[pp.v.isin(vs)].groupby("id")["median"].median()
        parts.append({"session_id": sid, "N": tags["N"], "profile": p, "vehicles": len(vs), "vehicle_seconds": len(vs) * duration,
                      "frames": int(sel.sum()), "id_counts": {f"0x{int(i):03X}": int(c) for i, c in zip(ids, cnt)},
                      "id_period_ms": {f"0x{int(i):03X}": round(float(v) / 1e6, 4) for i, v in pp.items()},
                      "dlc": dict(Counter(dlc[sel].tolist())), "byte_mean": pb[sel].mean(axis=0).round(3).tolist(),
                      "vehicle_frames": [vstats[v]["frames"] - vstats[v]["malicious"] for v in vs]})
    out["vehicle_stats"] = list(vstats.values())
    return out, parts


def work(sid):
    try:
        return session(sid)
    except Exception as e:  # noqa: BLE001
        return {"session_id": sid, "error": repr(e)}, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--sessions", nargs="*")
    a = ap.parse_args()
    out = Path(a.out).expanduser() / "work"
    out.mkdir(parents=True, exist_ok=True)
    sids = a.sessions or sorted(x.name for x in (DATA / "metadata").iterdir() if x.name.startswith("FCS10_"))
    done = set()
    sp = out / "session_stats.jsonl"
    if sp.exists() and not a.sessions:
        done = {json.loads(l)["session_id"] for l in sp.read_text().splitlines() if l.strip() and "error" not in json.loads(l)}
    todo = [s for s in sids if s not in done]
    with Pool(a.workers) as pool, open(sp, "a") as fs, open(out / "profile_parts.jsonl", "a") as fp:
        for i, (rec, parts) in enumerate(pool.imap_unordered(work, todo, chunksize=1)):
            fs.write(json.dumps(rec, default=float) + "\n")
            for p in parts:
                fp.write(json.dumps(p) + "\n")
            fs.flush()
            fp.flush()
            if "error" in rec:
                print("ERROR", rec["session_id"], rec["error"], flush=True)
            if (i + 1) % 25 == 0:
                print(f"{i + 1}/{len(todo)}", flush=True)
    print("done", len(todo))


if __name__ == "__main__":
    main()
