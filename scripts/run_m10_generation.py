"""Milestone 10: controlled generation of fleetcan-sim-scale-1.0 from the FROZEN matrix (matrix v1).

Authorisation: the frozen matrix keeps `approved_for_generation: false` (its bytes and SHA-256 are immutable). M10 was
authorised explicitly for exactly this matrix; the runner therefore requires
  --authorize-matrix-sha 9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735
and refuses to run if the argument, the constant below or the file's actual SHA-256 differ.

Per session (deterministic order: phase 1, phase 2, G0; config_index, repetition):
  1 classify the FINAL location with scripts/session_state.py (+ seed / provenance):
      COMPLETE   -> skip
      INCOMPLETE -> (absent / staged / partially published without metadata) clean and (re)generate
      INVALID    -> published but failing validation or provenance -> STOP, never overwritten
  2 disk gate (>= --min-free-gb free before starting) and memory gate (previous session peak RSS)
  3 write the per-session experiment / campaigns / attacks files (data/m10_configs/<sid>/), derived only from the matrix
  4 generator into a private staging root data/.m10_stage (the orchestrator itself stages in <root>/.incomplete
    and validates there), then the frozen descriptor analysis on the staging root
  5 verify: generator validation PASS, descriptor validation PASS, provenance tags, seed, versions, clean git,
    fleet/profiles, campaign + membership GT against the matrix (integer ns), isolated attacks, replay linkage,
    unique frame UIDs, transport/capture accounting, descriptor schema and finiteness
  6 publish atomically (os.replace, same filesystem): raw, ground_truth, validation, analysis, generated_sumo,
    metadata LAST; re-classify the final location (must be COMPLETE)
  7 ledger row + execution log; at the end of each phase a checkpoint incl. full fleetcan-sim-1.1 integrity
Any generation, validation or verification failure STOPS the run (stage kept for investigation).

Ledger / logs (git-ignored during generation, copied to results/m10_generation/ at the end):
  data/m10_generation/session_status.csv, execution_log.jsonl, checkpoint_<phase>.json, logs/<sid>.{gen,ana}.log

Usage:
  .venv/bin/python scripts/run_m10_generation.py --authorize-matrix-sha <sha> (--pilot | --phase 1 [--phase 2 ...] | --all)
  .venv/bin/python scripts/run_m10_generation.py --authorize-matrix-sha <sha> --status
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from session_state import clear_partial, session_state  # noqa: E402

MATRIX = ROOT / "configs" / "fleetcan_sim_scale_1_0.yaml"
EXPECTED_SHA = "9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735"
MATRIX_ID = "fleetcan-sim-scale-1.0-matrix-v1"
COMPONENT = "fleetcan-sim-scale-1.0"
DATA = ROOT / "data"
STAGE = DATA / ".m10_stage"
CFGS = DATA / "m10_configs"
RUN = DATA / "m10_generation"
LOGS = RUN / "logs"
LEDGER = RUN / "session_status.csv"
EXECLOG = RUN / "execution_log.jsonl"
LOCK = DATA / ".m10_runner.lock"
PY = str(ROOT / ".venv" / "bin" / "python")
PUBLISH = ("raw", "ground_truth", "validation", "analysis", "generated_sumo", "metadata")   # metadata last
BASE = ROOT / "data" / "final" / "fleetcan-sim-1.1"
BASE_HASHES = {"SHA256SUMS": "bc523937dc3c8e4d927ac892bd994bbe99b6a3ca51aa2fff56ba72c5761818c8",
               "FREEZE.json": "df58000aeb15229af5587d92d10922a2180d6bfae21a33c5d0c8b823e779a160",
               "manifest.jsonl": "aebfd8e07d5539ab9fb544c354b7c921b532209cd0dd535421167822d7e8e979"}
MEMORY_LIMIT_MB = 3000.0          # M8.1 max 1,414 MB (N = 100, 180 s); exceeding this stops the run for investigation
NS = 1_000_000_000
DESC_COLS = ["descriptor_id", "session_id", "vehicle_id", "window_index", "first_frame_uid", "last_frame_uid", "n_frames",
             "window_start_time", "window_end_time", "descriptor_timestamp", "window_start_ns", "window_end_ns",
             "descriptor_timestamp_ns", "time_basis", "nan_filled_features", "frame_count", "unique_can_id_count",
             "can_id_entropy", "most_common_can_id_ratio", "mean_inter_arrival_time", "std_inter_arrival_time", "mean_dlc",
             "std_dlc", *[f"byte_mean_{i}" for i in range(8)], *[f"byte_std_{i}" for i in range(8)]]
FEATURES = DESC_COLS[15:]
LEDGER_COLS = ["session_id", "config_id", "phase", "N", "K", "repetition", "seed", "scenario", "attack", "temporal_pattern",
               "status", "start_time", "end_time", "wall_time", "generation_wall_s", "analysis_wall_s", "frame_count",
               "descriptor_count", "storage_bytes", "peak_rss", "analysis_peak_rss", "peak_threads", "peak_fds",
               "temp_peak_mb", "free_disk_after_gb", "generator_validation", "descriptor_validation",
               "provenance_validation", "error"]


class Stop(Exception):
    pass


# ------------------------------------------------------------------ matrix
def load_matrix(authorized: str):
    raw = MATRIX.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if not (authorized == EXPECTED_SHA == sha):
        raise Stop(f"matrix SHA mismatch: authorized={authorized} expected={EXPECTED_SHA} actual={sha}")
    doc = yaml.load(raw, Loader=yaml.CSafeLoader)
    if (doc["matrix_id"], doc["component"], doc["matrix_status"], len(doc["configurations"]), len(doc["sessions"])) != \
            (MATRIX_ID, COMPONENT, "frozen", 167, 835):
        raise Stop("matrix identity/counts differ from the frozen v1 specification")
    return doc, sha


def ordered_sessions(doc):
    cfg = {c["config_id"]: c for c in doc["configurations"]}
    rank = {"1": 0, "2": 1, "G0": 2}
    ss = sorted(doc["sessions"], key=lambda s: (rank[str(cfg[s["config_id"]]["phase"])], s["config_index"], s["repetition"]))
    return ss, cfg


def pilot(doc):
    """Deterministic representatives (existing frozen research sessions; first matching configuration, repetition 1)."""
    want = [dict(N=20, scenario="S0"), dict(N=50, scenario="S1", attack="replay"),
            dict(N=100, scenario="S2", attack="flooding", independent_timing="near_simultaneous", independent_attack_count=5),
            dict(N=100, scenario="S3", attack="replay", K=100, temporal_pattern="simultaneous"),
            dict(N=100, scenario="S3", attack="flooding", K=100, temporal_pattern="simultaneous"),
            dict(N=50, scenario="S3", K=5, temporal_pattern="partial"),
            dict(N=100, scenario="S3", K=50, temporal_pattern="bounded_spread_10s"),
            dict(N=100, scenario="S3-mixed"),
            dict(phase="G0", scenario="S3", temporal_pattern="small")]
    out = []
    for w in want:
        c = next(c for c in doc["configurations"] if all(c.get(k) == v for k, v in w.items()))
        out.append(c["session_ids"][0])
    return out


# ------------------------------------------------------------------ helpers
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_gb() -> float:
    st = os.statvfs(str(DATA))
    return st.f_bavail * st.f_frsize / 1e9


def git_head_clean():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return head, not dirty


def vcan_ok(n: int) -> bool:
    out = subprocess.run(["ip", "-br", "link", "show", "type", "vcan"], capture_output=True, text=True).stdout.split("\n")
    up = {l.split()[0] for l in out if l.strip() and "UP" in l}
    return all(f"vcan{i}" in up for i in range(n))


def tree(pid):
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        try:
            for t in os.listdir(f"/proc/{p}/task"):
                todo += [int(c) for c in Path(f"/proc/{p}/task/{t}/children").read_text().split()]
        except OSError:
            pass
    return out


def sample(pid):
    rss = thr = fds = 0
    for p in tree(pid):
        try:
            st = Path(f"/proc/{p}/status").read_text().splitlines()
            rss += int(next(l for l in st if l.startswith("VmRSS")).split()[1])
            thr += int(next(l for l in st if l.startswith("Threads")).split()[1])
            fds += len(os.listdir(f"/proc/{p}/fd"))
        except (OSError, StopIteration, ValueError):
            pass
    return rss / 1024, thr, fds


def du(paths) -> int:
    tot = 0
    for p in paths:
        if p.exists():
            tot += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return tot


def sampled(cmd, log: Path, stage: Path | None = None):
    t0 = time.time()
    with open(log, "w") as fh:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
        peak = [0.0, 0, 0, 0.0]
        k = 0
        while p.poll() is None:
            r, t, f = sample(p.pid)
            peak[0], peak[1], peak[2] = max(peak[0], r), max(peak[1], t), max(peak[2], f)
            if stage is not None and k % 10 == 0:
                peak[3] = max(peak[3], du([stage]) / 1e6)
            k += 1
            time.sleep(0.5)
    return p.returncode, time.time() - t0, peak


# ------------------------------------------------------------------ ledger
def read_ledger(ss, cfg):
    rows = {}
    if LEDGER.exists():
        with open(LEDGER) as fh:
            rows = {r["session_id"]: r for r in csv.DictReader(fh)}
    out = []
    for s in ss:
        c = cfg[s["config_id"]]
        base = {k: "" for k in LEDGER_COLS}
        base.update(session_id=s["session_id"], config_id=c["config_id"], phase=c["phase"], N=c["N"], K=c["K"], repetition=s["repetition"],
                    seed=s["seed"], scenario=c["scenario"], attack=c["attack"],
                    temporal_pattern=c["temporal_pattern"] if c["scenario"] != "S2" else c["independent_timing"], status="PENDING")
        old = rows.get(s["session_id"])
        if old:
            base.update({k: old.get(k, "") for k in LEDGER_COLS if k not in ("session_id", "config_id", "phase", "N", "K", "repetition", "seed")})
        out.append(base)
    return out


def write_ledger(rows):
    RUN.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, LEDGER)


def log_event(ev: dict):
    RUN.mkdir(parents=True, exist_ok=True)
    with open(EXECLOG, "a") as fh:
        fh.write(json.dumps({"time": now(), **ev}) + "\n")


# ------------------------------------------------------------------ per-session configuration
def expected_tags(s, c, msha):
    coord = next((x["targets"] for x in s["campaigns"] if x["coordinated"]), [])
    ind = [x["targets"][0] for x in s["campaigns"] if not x["coordinated"]]
    return {"component": COMPONENT, "matrix_id": MATRIX_ID, "matrix_sha256": msha, "config_id": c["config_id"],
            "config_index": c["config_index"], "session_id": s["session_id"], "repetition": s["repetition"], "seed": s["seed"],
            "phase": str(c["phase"]), "purpose": c["purpose"], "N": c["N"], "K": c["K"], "scenario": c["scenario"], "attack": c["attack"],
            "temporal_pattern": c["temporal_pattern"], "independent_timing": c["independent_timing"], "route_source": c["route_source"],
            "member_selection": c["member_selection"], "coordinated_members": coord, "independent_targets": ind,
            "isolated_target": s["isolated_attacks"][0]["vehicle"] if s["isolated_attacks"] else "", **s["composition"]}


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def write_configs(s, c, doc, msha) -> Path:
    d = CFGS / s["session_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "campaigns.yaml").write_text("# derived from the frozen matrix " + MATRIX_ID + "\n"
                                      + yaml.safe_dump({"campaigns": s["campaigns"]}, sort_keys=False))
    (d / "attacks.yaml").write_text("# derived from the frozen matrix " + MATRIX_ID + "\n"
                                    + yaml.safe_dump({"attacks": s["isolated_attacks"]}, sort_keys=False))
    t = doc["experiment_template"]
    exp = {"experiment": {"experiment_id": s["session_id"], "dataset_version": COMPONENT, "scenario_id": c["scenario_id"],
                          "description": f"{COMPONENT} {c['config_id']} repetition {s['repetition']} ({MATRIX_ID})",
                          "seed": s["seed"], "tags": expected_tags(s, c, msha)},
           "simulation": {**t["simulation"], "duration_s": c["duration_s"]}, "can": t["can"], "output": {"root": str(STAGE)},
           "storage": t["storage"], "fleet_config": c["fleet_config"],
           "attacks_config": rel(d / "attacks.yaml"), "campaigns_config": rel(d / "campaigns.yaml")}
    (d / "experiment.yaml").write_text("# fleetcan-sim-scale-1.0 session configuration, derived from the frozen matrix "
                                       f"({MATRIX_ID}, sha256 {msha}); do not edit\n" + yaml.safe_dump(exp, sort_keys=False))
    return d / "experiment.yaml"


# ------------------------------------------------------------------ verification
def verify(s, c, root: Path, msha: str, head: str) -> tuple[list[str], dict]:
    import numpy as np
    import pyarrow.parquet as pq
    import m91_freeze as F
    from src.campaign_scheduler import build_schedule
    sid, P, info = s["session_id"], [], {}
    meta = json.loads((root / "metadata" / sid / "metadata.json").read_text())
    val = json.loads((root / "validation" / sid / "validation_report.json").read_text())
    dval = json.loads((root / "analysis" / sid / "descriptor_validation.json").read_text())
    info["generator_validation"], info["descriptor_validation"] = val["overall"], dval["overall"]
    # V26 (capture jitter of the host vCAN capture layer) may WARN under host load: non-authoritative, recorded only
    bad_checks = [x["id"] for x in val["checks"] if x["status"] not in ("PASS", "NA") and not (x["id"] == "V26" and x["status"] == "WARN")]
    info["warnings"] = [x["id"] for x in val["checks"] if x["status"] == "WARN"]
    if val["overall"] != "PASS" or bad_checks:
        P.append(f"generator validation {val['overall']}: {bad_checks}")
    if dval["overall"] != "PASS" or any(x["status"] != "PASS" for x in dval["checks"]):
        P.append(f"descriptor validation {dval['overall']}")
    # provenance
    tags = meta.get("tags") or {}
    exp = expected_tags(s, c, msha)
    bad = [k for k, v in exp.items() if tags.get(k) != v]
    if bad:
        P.append(f"provenance tags differ: {bad}")
    if meta.get("random_seed") != s["seed"] or meta.get("session_id") != sid or meta.get("scenario_id") != c["scenario_id"]:
        P.append("seed/session/scenario in metadata")
    if meta["versions"] != {"schema_version": "1.0", "generator_version": "1.3.0", "descriptor_version": "1.0", "dataset_version": COMPONENT}:
        P.append(f"versions {meta['versions']}")
    if meta["git"] != {"commit": head, "dirty": False}:
        P.append(f"git {meta['git']}")
    rg = meta["simulation"].get("route_generation") or {}
    if rg.get("method") != "grid_loops_v1":
        P.append("route source")
    # fleet / profiles
    fleet = F.load_fleet(c["fleet_config"], s["seed"])
    got = [(v["vehicle_id"], v["profile"]) for v in meta["fleet"]["vehicles"]]
    if got != [(a.vehicle_id, a.profile) for a in fleet] or meta["fleet"]["vehicle_count"] != c["N"]:
        P.append("fleet / profile assignment")
    prof = dict(got)
    comp = {f"N_{p}": sum(x == f"profile_{p}" for x in prof.values()) for p in "ABC"}
    coord = exp["coordinated_members"]
    comp.update({f"K_{p}": sum(prof[v] == f"profile_{p}" for v in coord) for p in "ABC"})
    if comp != s["composition"]:
        P.append(f"profile composition {comp}")
    # campaign ground truth vs matrix (integer ns from the frozen scheduler)
    rd = lambda t: pq.read_table(root / "ground_truth" / sid / f"{t}.parquet").to_pandas()
    camp, mem, att = rd("campaigns"), rd("campaign_membership"), rd("attacks")
    sched = {x.campaign_id: x for x in build_schedule(s["campaigns"], s["seed"], [a.vehicle_id for a in fleet])}
    if set(camp.campaign_id) != {x["campaign_id"] for x in s["campaigns"]} or len(camp) != len(s["campaigns"]):
        P.append("campaign set")
    n_coord = 0
    for x in s["campaigns"]:
        row = camp[camp.campaign_id == x["campaign_id"]]
        if len(row) != 1:
            continue
        row = row.iloc[0]
        if (str(row.coordinated) != str(x["coordinated"]).lower() or row.attack_type != x["attack_type"]
                or row.target_vehicles.split(";") != x["targets"] or int(row.campaign_start_ns) != int(round(x["start_s"] * NS))):
            P.append(f"campaign {x['campaign_id']} header")
        n_coord += x["coordinated"]
        m = mem[mem.campaign_id == x["campaign_id"]].set_index("vehicle_id")
        exp_start = {mm.vehicle_id: mm.start_ns for mm in sched[x["campaign_id"]].members}
        exp_end = {mm.vehicle_id: mm.end_ns for mm in sched[x["campaign_id"]].members}
        if set(m.index) != set(x["targets"]) or any(int(m.loc[v, "vehicle_attack_start_ns"]) != exp_start[v]
                                                     or int(m.loc[v, "vehicle_attack_end_ns"]) != exp_end[v]
                                                     or str(m.loc[v, "coordinated"]) != str(x["coordinated"]).lower() for v in x["targets"]):
            P.append(f"campaign {x['campaign_id']} membership/timing")
        if x["coordinated"] and c["temporal_pattern"] in ("bounded_spread_0p5s", "bounded_spread_2s", "bounded_spread_10s"):
            W = {"bounded_spread_0p5s": 0.5, "bounded_spread_2s": 2.0, "bounded_spread_10s": 10.0}[c["temporal_pattern"]]
            st = [int(m.loc[v, "vehicle_attack_start_ns"]) for v in x["targets"]]
            k = len(st)
            if abs((st[-1] - st[0]) - W * NS) > 1 or any(abs((st[i] - st[0]) - W * i / (k - 1) * NS) > 1 for i in range(k)):
                P.append("bounded spread timing")
    info["coordinated_campaigns"] = n_coord
    exp_att = {(f"{x['campaign_id']}_{v}", v) for x in s["campaigns"] for v in x["targets"]} | \
              {(a["attack_id"], a["vehicle"]) for a in s["isolated_attacks"]}
    if {(a, v) for a, v in zip(att.attack_id, att.vehicle_id)} != exp_att or len(att) != len(exp_att):
        P.append("attack set")
    for a in s["isolated_attacks"]:
        r = att[att.attack_id == a["attack_id"]]
        if len(r) != 1 or str(r.iloc[0].campaign_id) not in ("", "nan", "None") or int(r.iloc[0].start_ns) != int(round(a["start_s"] * NS)):
            P.append("isolated attack")
    # frames: unique UIDs, channel mapping
    fr = pq.read_table(root / "raw" / sid / "can_frames.parquet", columns=["frame_uid", "vehicle_id", "channel"]).to_pandas()
    info["frame_count"] = len(fr)
    if fr.frame_uid.nunique() != len(fr):
        P.append("duplicate frame_uid")
    chan = {v["vehicle_id"]: v["channel"] for v in meta["fleet"]["vehicles"]}
    chans = fr.groupby("vehicle_id").channel.agg(lambda x: set(x))
    if set(chans.index) != set(chan) or any(chans[v] != {chan[v]} for v in chans.index):
        P.append("vehicle/channel mapping")
    # replay linkage
    if c["attack"] == "replay":
        rm = rd("replay_map")
        windows = {}
        for x in s["campaigns"]:
            for mm in sched[x["campaign_id"]].members:
                windows[mm.attack_id] = (mm.vehicle_id, *mm.params["window_ns"])
        for a in s["isolated_attacks"]:
            w = a["params"]["window_s"]
            windows[a["attack_id"]] = (a["vehicle"], int(round(w[0] * NS)), int(round(w[1] * NS)))
        if set(rm.attack_id) != set(windows):
            P.append("replay attacks without linked frames")
        if (rm.source_vehicle != rm.target_vehicle).any():
            P.append("replay source substitution")
        src = fr.set_index("frame_uid")
        orig_ok = rm.original_frame_uid.isin(src.index)
        if not orig_ok.all():
            P.append("replay original frame missing")
        else:
            ov = src.loc[rm.original_frame_uid, "vehicle_id"].to_numpy()
            if (ov != rm.source_vehicle.to_numpy()).any():
                P.append("replay original on another bus")
        ns = lambda col: col.astype(str).map(lambda t: int(t.replace(".", "")) if "." in t and len(t.split(".")[1]) == 9 else int(round(float(t) * NS)))
        ot, rt = ns(rm.original_transmission_timestamp), ns(rm.replay_transmission_timestamp)
        for aid, g in rm.assign(ot=ot, rt=rt).groupby("attack_id"):
            veh, w0, w1 = windows[aid]
            if (g.target_vehicle != veh).any() or (g.ot < w0 - 1).any() or (g.ot >= w1 + 1).any():
                P.append(f"replay window {aid}")
            if not g.sort_values(["rt", "ot"]).ot.is_monotonic_increasing:
                P.append(f"replay order {aid}")
        info["replay_links"] = len(rm)
    # transport / capture
    for ch, v in meta["transport_stats"]["per_channel"].items():
        if v["send_errors"] or v["content_mismatch"] or v["missing"] or v["unexpected"] or not (v["sent"] == v["captured"] == v["matched"]):
            P.append(f"transport {ch}")
            break
    def timeouts(d):
        if isinstance(d, dict):
            return sum(timeouts(v) for k, v in d.items() if isinstance(v, (dict, list))) + \
                sum(v for k, v in d.items() if "timeout" in k.lower() and isinstance(v, (int, float)))
        if isinstance(d, list):
            return sum(timeouts(v) for v in d)
        return 0
    if timeouts(meta["transport_stats"]):
        P.append("flow-control timeouts")
    # descriptors
    dp = root / "analysis" / sid / "descriptors.parquet"
    if pq.read_schema(dp).names != DESC_COLS:
        P.append("descriptor schema")
    else:
        feat = pq.read_table(dp, columns=FEATURES).to_pandas().to_numpy(dtype=float)
        if not np.isfinite(feat).all():
            P.append("descriptor NaN/Inf")
        info["descriptor_count"] = len(feat)
    lab = pq.read_schema(root / "analysis" / sid / "descriptor_labels.parquet").names
    if set(lab) & set(FEATURES):
        P.append("labels contain features")
    return P, info


# ------------------------------------------------------------------ baseline integrity
def baseline_check() -> dict:
    r = subprocess.run(["sha256sum", "-c", "--quiet", "final/fleetcan-sim-1.1/SHA256SUMS"], cwd=DATA, capture_output=True, text=True)
    n = len((BASE / "SHA256SUMS").read_text().splitlines())
    hashes = {k: sha256(BASE / k) for k in BASE_HASHES}
    sessions = len((BASE / "manifest.jsonl").read_text().splitlines())
    writable = sum(1 for top in [BASE] + [DATA / d / x for d in ("raw", "ground_truth", "validation", "analysis", "metadata")
                                          for x in os.listdir(DATA / d) if x.startswith("FC11_")]
                   for f in [top, *top.rglob("*")] if os.access(f, os.W_OK))
    ok = r.returncode == 0 and n == 20805 and hashes == BASE_HASHES and sessions == 610 and writable == 0
    return {"status": "PASS" if ok else "FAIL", "files": n, "sha256sum_c_exit": r.returncode, "hashes_match": hashes == BASE_HASHES,
            "sessions": sessions, "writable": writable, "time": now()}


def checkpoint(name: str, sel, rows):
    """sel: ledger rows of this checkpoint; rows: whole ledger (for the cumulative count)."""
    done = [r for r in sel if r["status"] == "COMPLETE"]
    num = lambda k: sum(float(r[k] or 0) for r in done)
    cp = {"checkpoint": name, "expected": len(sel), "complete": len(done),
          "cumulative_complete": sum(r["status"] == "COMPLETE" for r in rows),
          "status_counts": dict(Counter(r["status"] for r in sel)), "frames": int(num("frame_count")),
          "descriptors": int(num("descriptor_count")), "storage_GB": round(num("storage_bytes") / 1e9, 3),
          "generation_plus_analysis_h": round(num("wall_time") / 3600, 3),
          "peak_rss_MB": max((float(r["peak_rss"] or 0) for r in done), default=0.0),
          "peak_analysis_rss_MB": max((float(r["analysis_peak_rss"] or 0) for r in done), default=0.0),
          "free_disk_GB": round(free_gb(), 2), "baseline": baseline_check(), "time": now()}
    (RUN / f"checkpoint_{name}.json").write_text(json.dumps(cp, indent=1))
    log_event({"event": "checkpoint", **{k: v for k, v in cp.items() if k != "status_counts"}})
    print(json.dumps(cp), flush=True)
    if cp["baseline"]["status"] != "PASS":
        raise Stop(f"baseline integrity FAILED at checkpoint {name}")
    return cp


# ------------------------------------------------------------------ main loop
def classify(s, c, msha):
    sid = s["session_id"]
    st = session_state(sid, DATA, expect={"matrix_id": MATRIX_ID, "matrix_sha256": msha, "config_id": c["config_id"],
                                          "session_id": sid, "component": COMPONENT})
    if st["state"] == "complete":
        meta = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
        ok = meta.get("random_seed") == s["seed"] and (meta.get("tags") or {}).get("seed") == s["seed"]
        return ("COMPLETE" if ok else "INVALID"), st
    if st["state"] in ("absent", "staged_incomplete", "partial_published"):
        return "INCOMPLETE", st
    return "INVALID", st


def clean_stage(sid):
    for d in PUBLISH + (".incomplete",):
        p = STAGE / d / sid
        if p.exists():
            shutil.rmtree(p)


def run_session(s, c, doc, msha, head, row, min_free):
    sid = s["session_id"]
    cls, st = classify(s, c, msha)
    if cls == "COMPLETE":
        row["status"] = "COMPLETE"
        return "skipped"
    if cls == "INVALID":
        row.update(status="INVALID", error=f"final state {st['state']}")
        raise Stop(f"{sid}: INVALID published session ({st['state']}); not overwritten")
    if st["state"] in ("staged_incomplete", "partial_published"):
        removed = clear_partial(sid, DATA)
        log_event({"event": "cleared_partial", "session_id": sid, "removed": removed})
    if free_gb() < min_free:
        raise Stop(f"free disk {free_gb():.2f} GB < {min_free} GB before {sid}")
    clean_stage(sid)
    exp = write_configs(s, c, doc, msha)
    LOGS.mkdir(parents=True, exist_ok=True)
    row.update(status="RUNNING", start_time=now(), error="")
    log_event({"event": "start", "session_id": sid, "config_id": c["config_id"], "phase": str(c["phase"]), "seed": s["seed"]})
    t0 = time.time()
    rc, gw, gpk = sampled([PY, "-m", "src.orchestrator", "--config", str(exp), "--session-id", sid], LOGS / f"{sid}.gen.log",
                          STAGE / ".incomplete" / sid)
    if rc != 0:
        row.update(status="FAILED", error=f"orchestrator exit {rc}")
        raise Stop(f"{sid}: orchestrator exit {rc} (see {LOGS / (sid + '.gen.log')})")
    rc, aw, apk = sampled([PY, "-m", "analysis.run_session", sid, "--data", str(STAGE)], LOGS / f"{sid}.ana.log")
    if rc != 0:
        row.update(status="FAILED", error=f"analysis exit {rc}")
        raise Stop(f"{sid}: analysis exit {rc}")
    problems, info = verify(s, c, STAGE, msha, head)
    row.update(generator_validation=info.get("generator_validation", ""), descriptor_validation=info.get("descriptor_validation", ""),
               provenance_validation="PASS" if not problems else "FAIL")
    if problems:
        row.update(status="FAILED", error="; ".join(problems)[:500])
        raise Stop(f"{sid}: verification failed: {problems}")
    for d in PUBLISH:
        if (DATA / d / sid).exists():
            raise Stop(f"{sid}: final {d} appeared during generation")
    for d in PUBLISH:                                           # metadata last
        src = STAGE / d / sid
        if src.exists():
            (DATA / d).mkdir(parents=True, exist_ok=True)
            os.replace(src, DATA / d / sid)
    clean_stage(sid)
    cls2, st2 = classify(s, c, msha)
    if cls2 != "COMPLETE":
        row.update(status="INVALID", error=f"after publish: {st2['state']}")
        raise Stop(f"{sid}: not COMPLETE after publication ({st2})")
    row.update(status="COMPLETE", end_time=now(), wall_time=round(time.time() - t0, 1), generation_wall_s=round(gw, 1),
               analysis_wall_s=round(aw, 1), frame_count=info["frame_count"], descriptor_count=info["descriptor_count"],
               storage_bytes=du([DATA / d / sid for d in PUBLISH]), peak_rss=round(gpk[0], 1), analysis_peak_rss=round(apk[0], 1),
               peak_threads=max(gpk[1], apk[1]), peak_fds=max(gpk[2], apk[2]), temp_peak_mb=round(gpk[3], 1),
               free_disk_after_gb=round(free_gb(), 2), error=("warnings: " + ",".join(info["warnings"])) if info["warnings"] else "")
    log_event({"event": "complete", "session_id": sid, "wall_s": row["wall_time"], "frames": info["frame_count"],
               "descriptors": info["descriptor_count"], "peak_rss_MB": row["peak_rss"], "replay_links": info.get("replay_links")})
    if gpk[0] > MEMORY_LIMIT_MB or apk[0] > MEMORY_LIMIT_MB:
        raise Stop(f"{sid}: peak RSS {gpk[0]:.0f}/{apk[0]:.0f} MB above {MEMORY_LIMIT_MB} MB; investigate memory")
    return "generated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--authorize-matrix-sha", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pilot", action="store_true")
    g.add_argument("--phase", action="append", choices=["1", "2", "G0"])
    g.add_argument("--all", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=15.0)
    a = ap.parse_args()
    try:
        doc, msha = load_matrix(a.authorize_matrix_sha)
    except Stop as e:
        sys.exit(f"STOP: {e}")
    ss, cfg = ordered_sessions(doc)
    rows = read_ledger(ss, cfg)
    byid = {r["session_id"]: r for r in rows}
    if a.status:
        for s in ss:
            cls, st = classify(s, cfg[s["config_id"]], msha)
            byid[s["session_id"]]["status"] = {"COMPLETE": "COMPLETE", "INVALID": "INVALID"}.get(cls, byid[s["session_id"]]["status"]
                                                                                                   if byid[s["session_id"]]["status"] == "FAILED" else "PENDING")
        print(json.dumps(dict(Counter(r["status"] for r in rows))))
        return
    if LOCK.exists():
        try:
            os.kill(int(LOCK.read_text()), 0)
            sys.exit(f"STOP: another runner is active (pid {LOCK.read_text()})")
        except (OSError, ValueError):
            pass
    LOCK.write_text(str(os.getpid()))
    try:
        head, clean = git_head_clean()
        if not clean:
            raise Stop("git working tree has modified tracked files; generation must run from a clean commit")
        if not vcan_ok(100):
            raise Stop("vcan0..vcan99 are not all UP (run sudo scripts/setup_vcan.sh 100)")
        b = baseline_check()
        log_event({"event": "run_start", "args": vars(a), "git_head": head, "matrix_sha256": msha, "free_GB": round(free_gb(), 2),
                   "baseline": b["status"]})
        if b["status"] != "PASS":
            raise Stop("baseline integrity failed before generation")
        if a.pilot:
            order = [next(s for s in ss if s["session_id"] == sid) for sid in pilot(doc)]
            phases = []
        else:
            want = ["1", "2", "G0"] if a.all else a.phase
            order = [s for s in ss if str(cfg[s["config_id"]]["phase"]) in want]
            phases = [p for p in ("1", "2", "G0") if p in want]
        done_phase = set()
        for s in order:
            c = cfg[s["config_id"]]
            res = run_session(s, c, doc, msha, head, byid[s["session_id"]], a.min_free_gb)
            write_ledger(rows)
            if res == "generated":
                print(f"{now()} COMPLETE {s['session_id']} wall={byid[s['session_id']]['wall_time']}s "
                      f"rss={byid[s['session_id']]['peak_rss']}MB free={byid[s['session_id']]['free_disk_after_gb']}GB", flush=True)
            ph = str(c["phase"])
            if phases and ph not in done_phase and all(byid[x["session_id"]]["status"] == "COMPLETE" for x in order if str(cfg[x["config_id"]]["phase"]) == ph):
                checkpoint(ph, [r for r in rows if str(r["phase"]) == ph], rows)
                done_phase.add(ph)
        if a.pilot:
            checkpoint("pilot", [byid[sid] for sid in pilot(doc)], rows)
        if a.all:
            checkpoint("all", rows, rows)
        log_event({"event": "run_end", "status": "OK"})
    except Stop as e:
        write_ledger(rows)
        log_event({"event": "run_stop", "reason": str(e)})
        print(f"STOP: {e}", flush=True)
        sys.exit(2)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
