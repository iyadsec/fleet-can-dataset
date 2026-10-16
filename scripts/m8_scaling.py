"""Milestone 8: generator scalability to 100 vehicles (engineering tests, NOT a research dataset).

  configs            write config/m8/ (fleets 10/20/50/100 with 3-digit IDs, generated routes; test experiments)
  run NAME...        run experiments with resource sampling -> results/m8_scalability/scalability_metrics.csv
  regress SID...     re-run frozen fleetcan-sim-1.1 configurations with the current code as M8_REG_<sid>
  verify SID...      scale-correctness checks per session  -> results/m8_scalability/verify/<sid>.json
  compare A B        logical comparison of two sessions (all tables, descriptors, metadata seeds/routes)
  interference A B   per-vehicle CAN equality of all vehicles except the flooded one

All test sessions are dataset_version "m8-scalability-test"; nothing in data/final/ is touched.
Usage: .venv/bin/python scripts/m8_scaling.py <cmd> [...]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from compare_sessions import compare  # noqa: E402
from final_requirements_audit import FEATURES, can_frame_bits, descriptor_features, expected_profiles, expected_switch_on, sub_seed  # noqa: E402
from src import route_generator  # noqa: E402
from src.config import load_experiment  # noqa: E402

DATA = ROOT / "data"
CFG = ROOT / "config" / "m8"
OUT = ROOT / "results" / "m8_scalability"
PY = str(ROOT / ".venv" / "bin" / "python")
DV = "m8-scalability-test"
SIZES = (10, 20, 50, 100)
FLOOD = "{can_id: 0x000, rate_hz: 1600, dlc: 8, payload: zeros}"
PARAMS = {"flooding": FLOOD, "fuzzy": "{mode: both, rate_hz: 1000, id_range: [0x000, 0x7FF], dlc: random}",
          "malfunction": "{mode: inject, target: vehicle_speed, manipulation: {strategy: constant, value: 250.0}, rate_hz: 100}"}
METRIC_FIELDS = ["session_id", "fleet_size", "seed", "scenario", "attack_type", "campaign_size", "simulation_duration_s", "wall_time_s",
                 "real_time_factor", "cpu_mean_percent", "cpu_peak_percent", "memory_start_mb", "memory_peak_mb", "memory_end_mb",
                 "thread_peak", "fd_peak", "socket_peak", "processes_peak", "vcan_interfaces", "frames_generated", "frames_transmitted",
                 "frames_captured", "queue_drops", "expired_drops", "capture_loss", "flow_control_timeouts", "descriptor_count",
                 "output_size_mb", "raw_mb", "ground_truth_mb", "descriptor_mb", "analysis_wall_s", "validation_status"]

EXPERIMENT = """# Milestone 8 scalability test (engineering only; not part of any research dataset).
experiment:
  experiment_id: {eid}
  dataset_version: {dv}
  scenario_id: {scen}
  description: "{desc}"
  seed: {seed}
  tags: {{milestone: M8, fleet_size: {n}, attack: {attack}, campaign_size: {size}, pattern: {pattern}}}
simulation:
  route_generation: {{method: grid_loops_v1, network: sumo/network/grid4x4.net.xml}}
  step_length_s: 0.01
  duration_s: {dur}
  realtime: false
can:
  transport: socketcan
  bus_model: logical
  frame_format: classical
  capture_drain_s: 0.5
output:
  root: data
storage:
  format: parquet
fleet_config: config/m8/fleets/fleet_{n}.yaml
attacks_config: {attacks}
campaigns_config: {campaigns}
"""
FLEET = """# Milestone 8 scalability fleet: frozen generator settings (A/B/C balanced_shuffle, switch-on U[0,1] s @1 us,
# +-1 % ECU jitter), only the vehicle-ID pattern is 3-digit (vehicle_001 .. vehicle_{n:03d}).
profiles_file: config/profiles/profiles_abc.yaml
fleet:
  channel_pattern: "vcan{{index}}"
  count: {n}
  id_pattern: "vehicle_{{n:03d}}"
  profile_assignment: {{strategy: balanced_shuffle, profiles: [profile_A, profile_B, profile_C]}}
  switch_on: {{range_s: [0.0, 1.0], resolution_s: 0.000001}}
  ecu_jitter: {{fraction: 0.01}}
"""


def vid(i: int) -> str:
    return f"vehicle_{i:03d}"


def camp(cid, fam, targets, start, delays, attack_s=5.0, coordinated=True, pattern="fixed"):
    lines = [f"  - campaign_id: {cid}", f"    attack_type: {fam}", f"    coordinated: {str(coordinated).lower()}",
             f"    targets: [{', '.join(targets)}]", f"    start_s: {start}"]
    if pattern == "simultaneous":
        lines.append("    delay_pattern: simultaneous")
    else:
        lines += ["    delay_pattern: fixed", f"    relative_delays_s: [{', '.join(f'{d:g}' for d in delays)}]"]
    if fam == "replay":
        lines.append(f"    params: {{window_relative_s: [-{attack_s}, 0.0], source_vehicle: self, time_offset_ms: 0.25}}")
    else:
        lines += [f"    duration_s: {attack_s:g}", f"    params: {PARAMS[fam]}"]
    return "\n".join(lines) + "\n"


def experiments() -> list[dict]:
    """Engineering test set (fixed here so it is reviewable)."""
    ex = []
    for n in SIZES:
        ex.append(dict(eid=f"M8_S0_N{n:03d}", n=n, seed=80000 + n, scen="S0", dur=40.0, attack="none", size=0, pattern="none",
                       desc=f"M8 benign benchmark, {n} vehicles"))
    ex += [
        dict(eid="M8_CAMP_N020_replay_s20_small", n=20, seed=82020, scen="S3", dur=10 + 9.5 + 10, attack="replay", size=20, pattern="small",
             body=camp("C_M8_REPLAY", "replay", [vid(i) for i in range(1, 21)], 10.0, [0.5 * i for i in range(20)])),
        dict(eid="M8_CAMP_N050_malfunction_s25_medium", n=50, seed=82050, scen="S3", dur=10 + 48 + 10, attack="malfunction", size=25,
             pattern="medium", body=camp("C_M8_MALF", "malfunction", [vid(i) for i in range(1, 26)], 10.0, [2.0 * i for i in range(25)])),
        dict(eid="M8_CAMP_N100_flooding_s100_simultaneous", n=100, seed=82100, scen="S3", dur=20.0, attack="flooding", size=100,
             pattern="simultaneous", body=camp("C_M8_FLOOD", "flooding", [vid(i) for i in range(1, 101)], 10.0, [0.0], pattern="simultaneous")),
        dict(eid="M8_CAMP_N100_fuzzy_s10_large", n=100, seed=82101, scen="S3", dur=10 + 90 + 10, attack="fuzzy", size=10, pattern="large",
             body=camp("C_M8_FUZZ", "fuzzy", [vid(i) for i in range(1, 11)], 10.0, [10.0 * i for i in range(10)])),
        dict(eid="M8_S2_N050_replay_10ind", n=50, seed=82150, scen="S2", dur=20.5, attack="replay", size=10, pattern="independent",
             body="".join(camp(f"C_M8_IND_{j + 1}", "replay", [vid(j + 1)], round(10.0 + 0.5 * j / 9, 4), [0.0], coordinated=False,
                               pattern="simultaneous") for j in range(10))),
        dict(eid="M8_ISO_N020_S0", n=20, seed=83020, scen="S0", dur=20.0, attack="none", size=0, pattern="none"),
        dict(eid="M8_ISO_N020_FLOOD_OVERSAT", n=20, seed=83020, scen="S1", dur=20.0, attack="flooding", size=1, pattern="oversat",
             attacks_body="attacks:\n  - attack_id: A_M8_OVERSAT\n    type: flooding\n    vehicle: vehicle_001\n    start_s: 10.0\n"
                          "    duration_s: 5.0\n    params: {can_id: 0x000, rate_hz: 5000, dlc: 8, payload: zeros}\n"),
    ]
    return ex


def write_configs() -> None:
    (CFG / "fleets").mkdir(parents=True, exist_ok=True)
    for n in SIZES:
        (CFG / "fleets" / f"fleet_{n}.yaml").write_text(FLEET.format(n=n))
    exps = experiments()
    # attack tests at N=100: per family 3 independent single-vehicle attacks on one vehicle of each profile
    probe = CFG / "_probe.yaml"
    probe.write_text(EXPERIMENT.format(eid="probe", dv=DV, scen="S0", desc="probe", seed=81100, n=100, attack="none", size=0,
                                       pattern="none", dur=20.0, attacks="config/attacks.yaml", campaigns="config/campaigns.yaml"))
    fleet = load_experiment(probe).fleet
    probe.unlink()
    targets = [next(a.vehicle_id for a in fleet if a.profile == p) for p in ("profile_A", "profile_B", "profile_C")]
    for fam in ("flooding", "fuzzy", "malfunction", "replay"):
        exps.append(dict(eid=f"M8_ATK_N100_{fam}_ABC", n=100, seed=81100, scen="S2", dur=20.5, attack=fam, size=3, pattern="independent",
                         body="".join(camp(f"C_M8_{fam.upper()}_{p}", fam, [t], 10.0 + 0.25 * j, [0.0], coordinated=False, pattern="simultaneous")
                                      for j, (p, t) in enumerate(zip("ABC", targets)))))
    for e in exps:
        d = CFG / "experiments"
        d.mkdir(parents=True, exist_ok=True)
        attacks, campaigns = "config/attacks.yaml", "config/campaigns.yaml"
        if e.get("body"):
            (d / f"{e['eid']}.campaigns.yaml").write_text("campaigns:\n" + e["body"])
            campaigns = f"config/m8/experiments/{e['eid']}.campaigns.yaml"
        if e.get("attacks_body"):
            (d / f"{e['eid']}.attacks.yaml").write_text(e["attacks_body"])
            attacks = f"config/m8/experiments/{e['eid']}.attacks.yaml"
        (d / f"{e['eid']}.yaml").write_text(EXPERIMENT.format(eid=e["eid"], dv=DV, scen=e["scen"], desc=e.get("desc", e["eid"]), seed=e["seed"],
                                                              n=e["n"], attack=e["attack"], size=e["size"], pattern=e["pattern"], dur=e["dur"],
                                                              attacks=attacks, campaigns=campaigns))
    print(f"{len(exps)} experiments; attack targets at N=100 (A, B, C): {targets}")


# ------------------------------------------------------------------ resource sampling
TICK = os.sysconf("SC_CLK_TCK")


def _tree(pid: int) -> list[int]:
    out, stack = [], [pid]
    while stack:
        p = stack.pop()
        out.append(p)
        try:
            for t in os.listdir(f"/proc/{p}/task"):
                stack += [int(c) for c in Path(f"/proc/{p}/task/{t}/children").read_text().split()]
        except OSError:
            pass
    return out


def _sample(pid: int) -> dict:
    rss = thr = fds = socks = cpu = 0
    procs = 0
    for p in _tree(pid):
        try:
            st = Path(f"/proc/{p}/status").read_text()
            rss += int(next(l for l in st.splitlines() if l.startswith("VmRSS")).split()[1])
            thr += int(next(l for l in st.splitlines() if l.startswith("Threads")).split()[1])
            fl = os.listdir(f"/proc/{p}/fd")
            fds += len(fl)
            for f in fl:
                try:
                    socks += os.readlink(f"/proc/{p}/fd/{f}").startswith("socket:")
                except OSError:
                    pass
            parts = Path(f"/proc/{p}/stat").read_text().rsplit(")", 1)[1].split()
            cpu += int(parts[11]) + int(parts[12])
            procs += 1
        except (OSError, StopIteration, ValueError):
            pass
    return {"t": time.perf_counter(), "rss_kb": rss, "threads": thr, "fds": fds, "sockets": socks, "cpu_ticks": cpu, "procs": procs}


def _mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6 if p.exists() else 0.0


def run_one(cfg_path: Path, sid: str) -> dict:
    proc = subprocess.Popen([PY, "-m", "src.orchestrator", "--config", str(cfg_path), "--session-id", sid], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    samples = []
    t0 = time.perf_counter()
    while proc.poll() is None:
        samples.append(_sample(proc.pid))
        time.sleep(0.2)
    out = proc.stdout.read()
    wall = time.perf_counter() - t0
    (OUT / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "logs" / f"{sid}.log").write_text(out)
    if proc.returncode != 0:
        raise RuntimeError(f"{sid}: orchestrator exit {proc.returncode}\n{out[-3000:]}")
    ta = time.perf_counter()
    an = subprocess.run([PY, "-m", "analysis.run_session", sid], cwd=ROOT, capture_output=True, text=True)
    an_wall = time.perf_counter() - ta
    md = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
    val = json.loads((DATA / "validation" / sid / "validation_report.json").read_text())
    dval = json.loads((DATA / "analysis" / sid / "descriptor_validation.json").read_text())
    samples = [s for s in samples if s["procs"]]
    cpu = [100.0 * (b["cpu_ticks"] - a["cpu_ticks"]) / TICK / (b["t"] - a["t"]) for a, b in zip(samples, samples[1:]) if b["t"] > a["t"]]
    bt = md["can"]["bus_totals"]
    tot = md["transport_stats"]["total"]
    sim_s = md["simulation"]["sim_end_ns"] / 1e9
    ndesc = len(pd.read_parquet(DATA / "analysis" / sid / "descriptors.parquet", columns=["descriptor_id"]))
    raw_mb, gt_mb, an_mb = _mb(DATA / "raw" / sid), _mb(DATA / "ground_truth" / sid), _mb(DATA / "analysis" / sid)
    tags = md.get("tags", {})
    row = {"session_id": sid, "fleet_size": md["fleet"]["vehicle_count"], "seed": md["random_seed"], "scenario": md["scenario_id"],
           "attack_type": tags.get("attack"), "campaign_size": tags.get("campaign_size"), "simulation_duration_s": sim_s,
           "wall_time_s": round(md["simulation"]["execution"]["wall_seconds"], 2),
           "real_time_factor": round(sim_s / md["simulation"]["execution"]["wall_seconds"], 4),
           "cpu_mean_percent": round(float(np.mean(cpu)), 1) if cpu else None, "cpu_peak_percent": round(float(np.max(cpu)), 1) if cpu else None,
           "memory_start_mb": round(samples[0]["rss_kb"] / 1024, 1), "memory_peak_mb": round(max(s["rss_kb"] for s in samples) / 1024, 1),
           "memory_end_mb": round(samples[-1]["rss_kb"] / 1024, 1), "thread_peak": max(s["threads"] for s in samples),
           "fd_peak": max(s["fds"] for s in samples), "socket_peak": max(s["sockets"] for s in samples),
           "processes_peak": max(s["procs"] for s in samples), "vcan_interfaces": len(md["fleet"]["vehicles"]),
           "frames_generated": sum(v["generated"] for v in bt.values()), "frames_transmitted": sum(v["transmitted"] for v in bt.values()),
           "frames_captured": tot.get("captured"), "queue_drops": sum(v["queue_dropped"] for v in bt.values()),
           "expired_drops": sum(v["expired_dropped"] for v in bt.values()),
           "capture_loss": sum(v["transmitted"] for v in bt.values()) - int(tot.get("captured", 0)),
           "flow_control_timeouts": md["simulation"]["execution"]["flow_control_timeouts"], "descriptor_count": ndesc,
           "output_size_mb": round(raw_mb + gt_mb + an_mb + _mb(DATA / "metadata" / sid) + _mb(DATA / "validation" / sid), 2),
           "raw_mb": round(raw_mb, 2), "ground_truth_mb": round(gt_mb, 2), "descriptor_mb": round(an_mb, 2), "analysis_wall_s": round(an_wall, 2),
           "validation_status": f"generator={val['overall']};descriptor={dval['overall']};analysis_exit={an.returncode}"}
    f = OUT / "scalability_metrics.csv"
    new = not f.exists()
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=METRIC_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    print(json.dumps(row), flush=True)
    return row


# ------------------------------------------------------------------ verification
def rd(sid, sub, name, cols=None):
    return pd.read_parquet(DATA / sub / sid / f"{name}.parquet", columns=cols)


def verify(sid: str) -> dict:
    md = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
    seed, veh = md["random_seed"], md["fleet"]["vehicles"]
    n = len(veh)
    issues = []

    def need(c, m):
        if not c:
            issues.append(m)

    vids = [v["vehicle_id"] for v in veh]
    pat = "vehicle_{:03d}" if len(vids[0]) == 11 else "vehicle_{:02d}"
    need(vids == [pat.format(i) for i in range(1, n + 1)] and len(set(vids)) == n, "vehicle IDs")
    need([v["channel"] for v in veh] == [f"vcan{i}" for i in range(n)] and len({v["channel"] for v in veh}) == n, "one distinct CAN bus/vCAN per vehicle")
    need([v["profile"] for v in veh] == expected_profiles(seed, n), "profile assignment != seed re-derivation")
    need([int(v["switch_on_ns"]) for v in veh] == expected_switch_on(seed, n), "switch-on != seed re-derivation")
    prof = {v["vehicle_id"]: v["profile"] for v in veh}
    counts = {p[-1]: sum(v["profile"] == p for v in veh) for p in ("profile_A", "profile_B", "profile_C")}
    rg = md["simulation"].get("route_generation")
    if rg:
        xml, info = route_generator.generate(ROOT / "sumo/network/grid4x4.net.xml", vids, sub_seed(seed, "route_generation"), md["simulation"]["duration_s_configured"])
        need(info["route_sha256"] == rg["route_sha256"] and rg["route_seed"] == sub_seed(seed, "route_generation"), "route file not reproducible from seed")
    st = rd(sid, "raw", "vehicle_states", ["sim_time_ns", "vehicle_id"])
    st["sim_time_ns"] = st.sim_time_ns.astype("int64")
    first_t = st.sim_time_ns.min()
    need(set(st[st.sim_time_ns == first_t].vehicle_id) == set(vids), "not every vehicle present at the first SUMO step")
    need(st.groupby("vehicle_id").size().nunique() == 1 and set(st.vehicle_id) == set(vids), "vehicle missing / leaves early in SUMO")
    need(set(np.diff(np.sort(st.sim_time_ns.unique()))) == {10_000_000}, "SUMO step != 10 ms")
    fr = rd(sid, "raw", "can_frames")
    lab = rd(sid, "ground_truth", "frame_labels")
    for c in ("sim_time_ns", "tx_start_ns", "tx_end_ns", "frame_bits", "dlc"):
        fr[c] = fr[c].astype("int64")
    fr["can_id"] = fr.can_id.map(lambda x: int(x, 0))
    need(fr.frame_uid.is_unique and len(lab) == len(fr) and set(lab.frame_uid) == set(fr.frame_uid), "frame_uid unique / labels 1:1")
    need(set(fr.vehicle_id) == set(vids), "a vehicle produced no CAN frames")
    need(bool((fr.groupby("vehicle_id").channel.nunique() == 1).all())
         and all(fr[fr.vehicle_id == v].channel.iloc[0] == c for v, c in zip(vids, (x["channel"] for x in veh))), "frames on a foreign channel")
    ov = sum(int((g.sort_values("tx_start_ns").tx_start_ns.to_numpy()[1:] < g.sort_values("tx_start_ns").tx_end_ns.to_numpy()[:-1]).sum())
             for _, g in fr.groupby("vehicle_id"))
    need(ov == 0, f"{ov} overlapping transmissions on a vehicle bus")
    samp = fr.sample(min(500, len(fr)), random_state=0)
    need(all(can_frame_bits(c, bytes.fromhex(p)) == b and e - s == b * 2000 for c, p, b, s, e in
             zip(samp.can_id, samp.payload, samp.frame_bits, samp.tx_start_ns, samp.tx_end_ns)), "frame bit count / duration")
    fr = fr.merge(lab[["frame_uid", "frame_label", "message_name"]], on="frame_uid")
    mal = fr.frame_label == "malicious"
    # jitter re-derivation for every benign stream
    import yaml
    P = yaml.safe_load((ROOT / "config/profiles/profiles_abc.yaml").read_text())["profiles"]
    msgs = {p: {m["name"]: m for m in P[p]["messages"]} for p in P}
    t0 = rd(sid, "raw", "vehicle_states", ["sim_time_ns", "vehicle_id"]).astype({"sim_time_ns": "int64"}).groupby("vehicle_id").sim_time_ns.min()
    so = {v["vehicle_id"]: int(v["switch_on_ns"]) for v in veh}
    jseed = sub_seed(seed, "ecu_jitter")
    mism = 0
    drops = rd(sid, "ground_truth", "dropped_frames")
    gen = pd.concat([fr.loc[~mal, ["vehicle_id", "message_name", "sim_time_ns"]],
                     drops.loc[drops.frame_label == "benign", ["vehicle_id", "message_name", "sim_time_ns"]].astype({"sim_time_ns": "int64"})])
    for (v, m), g in gen.groupby(["vehicle_id", "message_name"]):
        spec = msgs[prof[v]][m]
        Pn, off = round(spec["period_ms"] * 1e6), round(spec["offset_ms"] * 1e6)
        t = np.sort(g.sim_time_ns.to_numpy())
        nominal = int(t0[v]) + so[v] + off + np.arange(len(t)) * Pn
        rel = nominal + np.round(np.random.default_rng(sub_seed(jseed, f"ecu_jitter/{v}/{m}")).uniform(-0.01, 0.01, size=len(t)) * Pn).astype(np.int64)
        rel[0] = max(int(t0[v]), rel[0])
        mism += int((rel != t).sum())
    need(mism == 0, f"{mism} ECU release times differ from the seed re-derivation")
    # descriptors: structure for all, full reconstruction for first, middle and last vehicle
    desc = rd(sid, "analysis", "descriptors")
    dl = rd(sid, "analysis", "descriptor_labels")
    need(list(desc.columns[-24:]) == FEATURES and desc.descriptor_id.is_unique and len(dl) == len(desc), "descriptor schema / ids")
    nper = fr.groupby("vehicle_id").size()
    need(all((desc.vehicle_id == v).sum() == (nper[v] - 100) // 50 + 1 for v in vids), "window counts")
    need(bool((desc.descriptor_timestamp_ns == desc.window_end_ns).all()), "descriptor timestamp != window end")
    err, lab_err, nrec = 0.0, 0, 0
    desc = desc.merge(dl[["descriptor_id", "malicious_frame_count"]], on="descriptor_id")
    for v in sorted({vids[0], vids[n // 2], vids[-1]}):
        g = fr[fr.vehicle_id == v].sort_values(["tx_end_ns", "frame_uid"]).reset_index(drop=True)
        for d in desc[desc.vehicle_id == v].itertuples():
            k = int(d.window_index)
            w = g.iloc[k * 50: k * 50 + 100]
            x = descriptor_features(w.can_id.to_numpy(), w.dlc.to_numpy(), list(w.payload), w.tx_end_ns.to_numpy())
            s = np.array([float(getattr(d, f)) for f in FEATURES])
            err = max(err, float(np.max(np.abs(x - s) / np.maximum(1.0, np.abs(s)))))
            lab_err += int(w.frame_uid.iloc[0] != d.first_frame_uid or w.frame_uid.iloc[-1] != d.last_frame_uid
                           or int((w.frame_label == "malicious").sum()) != int(d.malicious_frame_count))
            nrec += 1
    need(err < 1e-9 and lab_err == 0, f"descriptor reconstruction err={err} span/label mismatches={lab_err}")
    # ground truth at scale
    att = rd(sid, "ground_truth", "attacks")
    memb = rd(sid, "ground_truth", "campaign_membership")
    camps = rd(sid, "ground_truth", "campaigns")
    rmap = rd(sid, "ground_truth", "replay_map")
    attacked = sorted(set(fr.loc[mal, "vehicle_id"]))
    need(set(fr.loc[mal, "vehicle_id"]) <= set(att.vehicle_id), "malicious frames on a vehicle without attack GT")
    res_c = {}
    if len(camps):
        for c in camps.itertuples():
            mm = memb[memb.campaign_id == c.campaign_id].sort_values("vehicle_id")
            res_c[c.campaign_id] = {"coordinated": c.coordinated, "members": len(mm), "first": mm.vehicle_id.iloc[0], "last": mm.vehicle_id.iloc[-1],
                                    "delays_s": sorted({round(int(x) / 1e9, 4) for x in mm.relative_delay_ns})[:3] + ["..."] + sorted({round(int(x) / 1e9, 4) for x in mm.relative_delay_ns})[-2:]}
            need(set(mm.attack_id) == set(att[att.campaign_id == c.campaign_id].attack_id), f"{c.campaign_id}: member attack IDs")
    if len(rmap):
        need(bool((rmap.source_vehicle == rmap.target_vehicle).all()), "replay not own vehicle")
        src = fr.set_index("frame_uid").reindex(rmap.original_frame_uid)
        need(bool(src.frame_label.eq("benign").all() and (src.vehicle_id.to_numpy() == rmap.source_vehicle.to_numpy()).all()), "replay source links")
    val = json.loads((DATA / "validation" / sid / "validation_report.json").read_text())
    dval = json.loads((DATA / "analysis" / sid / "descriptor_validation.json").read_text())
    res = {"session_id": sid, "fleet_size": n, "profiles": counts, "ok": not issues, "issues": issues,
           "generator_validation": val["overall"], "descriptor_validation": dval["overall"],
           "non_pass_checks": sorted({c["id"] + ":" + c["status"] for c in val["checks"] + dval["checks"] if c["status"] not in ("PASS", "NA")}),
           "frames": len(fr), "malicious_frames": int(mal.sum()), "attacked_vehicles": len(attacked), "attacked_first_last": attacked[:1] + attacked[-1:],
           "attacked_profiles": sorted({prof[v][-1] for v in attacked}), "replay_links": len(rmap), "campaigns": res_c,
           "descriptors": len(desc), "reconstructed_descriptors": nrec, "recon_max_rel_error": err, "route_loops": (rg or {}).get("loop_counts")}
    (OUT / "verify").mkdir(parents=True, exist_ok=True)
    (OUT / "verify" / f"{sid}.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("session_id", "ok", "issues", "generator_validation", "descriptor_validation", "non_pass_checks", "profiles",
                                          "frames", "attacked_vehicles", "attacked_profiles", "campaigns", "reconstructed_descriptors", "recon_max_rel_error",
                                          "route_loops")}, default=str), flush=True)
    return res


def cmp(a: str, b: str) -> dict:
    res = compare(DATA, a, b)
    ma, mb = (json.loads((DATA / "metadata" / s / "metadata.json").read_text()) for s in (a, b))
    keys = {"profiles+switch_on": [(v["vehicle_id"], v["profile"], v["switch_on_ns"]) for v in ma["fleet"]["vehicles"]] ==
                                  [(v["vehicle_id"], v["profile"], v["switch_on_ns"]) for v in mb["fleet"]["vehicles"]],
            "assignment/jitter seeds": ma["fleet"]["assignment"] == mb["fleet"]["assignment"],
            "route_generation": ma["simulation"].get("route_generation") == mb["simulation"].get("route_generation"),
            "campaign_schedule": ma.get("campaign_schedule") == mb.get("campaign_schedule"),
            "attacks_runtime": ma.get("attacks_runtime") == mb.get("attacks_runtime")}
    out = {"a": a, "b": b, "tables": res, "metadata": keys,
           "identical": all(v.startswith("IDENTICAL") for v in res.values()) and all(keys.values())}
    (OUT / "compare").mkdir(parents=True, exist_ok=True)
    (OUT / "compare" / f"{a}__vs__{b}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return out


def interference(a: str, b: str, flooded: str = "vehicle_001") -> dict:
    """a = S0 run, b = same seed with an over-saturating flood on `flooded`: every OTHER vehicle's CAN traffic
    (generation, arbitration, tx times, payloads, drops) and descriptors must be identical."""
    cols = ["vehicle_id", "sim_time_ns", "tx_start_ns", "tx_end_ns", "queue_delay_us", "frame_bits", "can_id", "dlc", "payload"]
    fa, fb = rd(a, "raw", "can_frames", cols), rd(b, "raw", "can_frames", cols)
    da, db = rd(a, "analysis", "descriptors"), rd(b, "analysis", "descriptors")
    feat = ["vehicle_id", "window_index", "descriptor_timestamp_ns", *FEATURES]
    res = {}
    for v in sorted(set(fa.vehicle_id)):
        x = fa[fa.vehicle_id == v].sort_values(cols).reset_index(drop=True)
        y = fb[fb.vehicle_id == v].sort_values(cols).reset_index(drop=True)
        dx = da[da.vehicle_id == v][feat].reset_index(drop=True)
        dy = db[db.vehicle_id == v][feat].reset_index(drop=True)
        res[v] = {"frames_equal": bool(x.equals(y)), "descriptors_equal": bool(dx.equals(dy)), "frames": [len(x), len(y)]}
    others = {v: r for v, r in res.items() if v != flooded}
    mb = json.loads((DATA / "metadata" / b / "metadata.json").read_text())
    out = {"a": a, "b": b, "flooded": flooded, "flooded_vehicle": res[flooded],
           "flooded_bus_totals": mb["can"]["bus_totals"][next(x["channel"] for x in mb["fleet"]["vehicles"] if x["vehicle_id"] == flooded)],
           "other_vehicles": len(others), "other_vehicles_identical": all(r["frames_equal"] and r["descriptors_equal"] for r in others.values())}
    (OUT / "compare").mkdir(parents=True, exist_ok=True)
    (OUT / "compare" / f"interference_{a}__vs__{b}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["configs", "run", "regress", "verify", "compare", "interference"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--suffix", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "configs":
        write_configs()
    elif a.cmd == "run":
        for name in a.args:
            run_one(CFG / "experiments" / f"{name}.yaml", name + a.suffix)
    elif a.cmd == "regress":
        spec = {r["session_id"]: r for r in json.loads((ROOT / "config/final/matrix/matrix_spec.json").read_text())["runs"]}
        (CFG / "regression").mkdir(parents=True, exist_ok=True)
        for sid in a.args:
            # identical frozen configuration; only the descriptive dataset_version label differs so the re-run is
            # never mistaken for a fleetcan-sim-1.1 session (it does not affect generation)
            src = (ROOT / spec[sid]["experiment_config"]).read_text()
            assert "dataset_version: fleetcan-sim-1.1\n" in src
            p = CFG / "regression" / f"{sid}.yaml"
            p.write_text(src.replace("dataset_version: fleetcan-sim-1.1\n", "dataset_version: m8-regression-test\n"))
            run_one(p, "M8_REG_" + sid)
    elif a.cmd == "verify":
        for sid in a.args:
            verify(sid)
    elif a.cmd == "compare":
        cmp(a.args[0], a.args[1])
    elif a.cmd == "interference":
        interference(a.args[0], a.args[1])
