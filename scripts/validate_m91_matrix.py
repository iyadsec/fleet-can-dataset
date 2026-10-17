"""Milestone 9.1: READ-ONLY validator of the FROZEN matrix configs/fleetcan_sim_scale_1_0.yaml and all planned sessions.

Generates no CAN data, starts no SUMO, writes nothing under data/. Every planned session is additionally loaded
through the real configuration / campaign-scheduler / attack-engine code (as the M6 preflight did) from temporary
experiment files, so the generator would accept it exactly as frozen.

Checks (C01-C26):
  C01 167 configurations          C02 835 sessions                 C03 R = 5, repetition / session-id mapping
  C04 G0 exactly as approved       C05 G1 absent                    C06 config IDs (unique, format, no collisions)
  C07 session IDs (unique, none exists, expected_sessions.csv)      C08 seeds (formula, unique, no collisions)
  C09 K <= N                       C10 member feasibility           C11 profile balance / S1 target profiles
  C12 scenario validity + generator preflight                       C13 attack validity, frozen parameters
  C14 timing validity              C15 bounded-spread mathematics   C16 partial-overlap semantics
  C17 duration sufficiency         C18 S2 coordinated = false       C19 S3 coordinated = true
  C20 matrix_status frozen         C21 approved_for_generation false C22 no generation runner consumes the matrix
  C23 deterministic rebuild + freeze manifest identity              C24 consistency with the approved M9 proposal
  C25 frozen code / traffic semantics unchanged since the generator source commit
  C26 provenance requirements declared

Usage: .venv/bin/python scripts/validate_m91_matrix.py [--matrix P] [--sessions-csv P] [--manifest P|none]
                                                        [--no-preflight] [--report P]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import m9_design as D  # noqa: E402
import m91_freeze as F  # noqa: E402
from src.campaign_scheduler import build_schedule, check_scenario, pair_overlaps  # noqa: E402
from validate_m9_matrix import existing_ids_and_seeds  # noqa: E402

NS = 1_000_000_000
EPS = 1e-9
ALLOWED_REFS = ("m91_freeze.py", "validate_m91_matrix.py")
SCEN_PATTERNS = {"S0": {"none"}, "S1": {"isolated"}, "S2": {"independent"},
                 "S3": {"simultaneous", "small", "medium", "large", "partial", *D.BOUNDED}, "S3-probe": {"probe"}, "S3-mixed": {"small"}}
CID_RE = re.compile(r"^FCS10_(G0_)?N\d{3}_(S0|S1|S2|S3|S3-probe|S3-mixed)_(none|flooding|fuzzy|malfunction|replay)_K\d{3}_[A-Za-z0-9_]+$")


class Report:
    def __init__(self):
        self.checks = {}

    def add(self, cid, ok, detail):
        self.checks[cid] = {"status": "PASS" if ok else "FAIL", "detail": detail}

    @property
    def ok(self):
        return all(c["status"] == "PASS" for c in self.checks.values())


def baseline_params():
    """Attack parameters exactly as written in the frozen fleetcan-sim-1.1 matrix."""
    base = ROOT / "config" / "final" / "matrix"
    out = {}
    for f in D.FAMILIES:
        c = yaml.safe_load((base / f"N10_S3_{f}_s10_simultaneous" / "campaigns.yaml").read_text())["campaigns"][0]
        out[("campaign", f, 5.0)] = (c["params"], c.get("duration_s"))
        a = yaml.safe_load((base / f"N10_S1_{f}_s01_isolated" / "attacks.yaml").read_text())["attacks"][0]
        out[("isolated", f)] = (a["params"], a.get("duration_s"), a["start_s"])
    p = yaml.safe_load((base / "N10_S3_replay_s10_partial" / "campaigns.yaml").read_text())["campaigns"][0]
    out[("campaign", "replay", 10.0)] = (p["params"], None)
    q = yaml.safe_load((base / "N10_S3_flooding_s10_partial" / "campaigns.yaml").read_text())["campaigns"][0]
    out[("campaign", "flooding", 10.0)] = (q["params"], q["duration_s"])
    return out


def engineering_ids():
    ids = set()
    for f in (ROOT / "config").rglob("*.yaml"):
        for m in re.finditer(r"experiment_id:\s*([A-Za-z0-9_.\-]+)", f.read_text()):
            ids.add(m.group(1))
    spec = json.loads((ROOT / "config" / "final" / "matrix" / "matrix_spec.json").read_text())
    base_cfg = {r["config_id"] for r in spec["runs"]} | {f"FC11_{r['config_id']}" for r in spec["runs"]}
    return ids, base_cfg


def write_experiment(tmp: Path, c: dict, s: dict, tpl: dict) -> Path:
    camp = tmp / "campaigns.yaml"
    atk = tmp / "attacks.yaml"
    camp.write_text(yaml.safe_dump({"campaigns": s["campaigns"]}, sort_keys=False))
    atk.write_text(yaml.safe_dump({"attacks": s["isolated_attacks"]}, sort_keys=False))
    exp = {"experiment": {"experiment_id": s["session_id"], "dataset_version": tpl["dataset_version"], "scenario_id": c["scenario_id"],
                          "description": "M9.1 preflight (not executed)", "seed": s["seed"], "tags": {"preflight": True}},
           "simulation": {**tpl["simulation"], "duration_s": c["duration_s"]}, "can": tpl["can"], "output": {"root": str(tmp / "out")},
           "storage": tpl["storage"], "fleet_config": c["fleet_config"], "attacks_config": str(atk), "campaigns_config": str(camp)}
    p = tmp / "experiment.yaml"
    p.write_text(yaml.safe_dump(exp, sort_keys=False))
    return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=str(F.MATRIX))
    ap.add_argument("--sessions-csv", default=str(F.SESSIONS_CSV))
    ap.add_argument("--manifest", default=str(F.MANIFEST), help="path or 'none'")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    if a.report and Path(a.report).resolve().is_relative_to((ROOT / "data").resolve()):
        sys.exit("refusing to write into data/")
    mbytes = Path(a.matrix).read_bytes()
    doc = yaml.load(mbytes, Loader=yaml.CSafeLoader)
    rows, sessions = doc["configurations"], doc["sessions"]
    byid = {r["config_id"]: r for r in rows}
    R = Report()

    # C01-C03 ---------------------------------------------------------------------------------------------------------
    R.add("C01_167_configurations", len(rows) == 167 == doc["counts"]["configurations"], {"configurations": len(rows)})
    R.add("C02_835_sessions", len(sessions) == 835 == doc["counts"]["sessions"] == sum(len(r["session_ids"]) for r in rows),
          {"sessions": len(sessions)})
    bad = [r["config_id"] for r in rows if r["repetitions"] != 5 or len(r["seeds"]) != 5 or len(r["session_ids"]) != 5
           or r["session_ids"] != [f"{r['config_id']}_R{i}" for i in range(1, 6)]]
    reps = Counter((s["config_id"], s["repetition"]) for s in sessions)
    bad += [s["session_id"] for s in sessions if s["config_id"] not in byid
            or s["session_id"] != f"{s['config_id']}_R{s['repetition']}" or reps[(s["config_id"], s["repetition"])] != 1]
    R.add("C03_repetitions", not bad and doc["counts"]["repetitions"] == 5, {"bad": bad[:5]})

    # C04-C05 ---------------------------------------------------------------------------------------------------------
    g0 = [r for r in rows if r["purpose"] == "route_source_bridge" or r["phase"] == "G0" or "_G0_" in r["config_id"]]
    shape = sorted((r["scenario"], r["K"], r["temporal_pattern"]) for r in g0)
    want = sorted([("S0", 0, "none"), ("S3", 5, "simultaneous"), ("S3", 5, "simultaneous"), ("S3", 5, "small"), ("S3", 5, "small")])
    fams = {p: sorted(r["attack"] for r in g0 if r["temporal_pattern"] == p) for p in ("simultaneous", "small")}
    ok = (len(g0) == 5 and shape == want and all(r["N"] == 10 and r["purpose"] == "route_source_bridge" and r["phase"] == "G0"
                                                   and "_G0_" in r["config_id"] and r["fleet_config"] == F.G0_FLEET
                                                   and r["member_selection"] == "baseline_first_k" and r["repetitions"] == 5 for r in g0)
          and all(len(set(v)) == 2 for v in fams.values()) and sum(len(r["session_ids"]) for r in g0) == 25)
    R.add("C04_G0_exact", ok, {"configs": [r["config_id"] for r in g0], "families": fams})
    g1 = [r["config_id"] for r in rows if r["N"] == 10 and (r["purpose"] != "route_source_bridge"
                                                            or r["temporal_pattern"] in ("medium", "large", "partial"))]
    ns = sorted({r["N"] for r in rows})
    R.add("C05_G1_absent", not g1 and ns == [10, 20, 50, 100], {"G1_like": g1, "fleet_sizes": ns})

    # C06-C08 ---------------------------------------------------------------------------------------------------------
    eng, base_cfg = engineering_ids()
    ex_sids, ex_seeds, n_meta = existing_ids_and_seeds()
    for d in ("generated_sumo",):
        for p in (ROOT / "data" / d).glob("*"):
            ex_sids.add(p.name)
    cids = [r["config_id"] for r in rows]
    fmt = [x for x in cids if not CID_RE.match(x)]
    coll = sorted(set(cids) & (base_cfg | eng | ex_sids))
    R.add("C06_config_ids", len(cids) == len(set(cids)) and not fmt and not coll
          and [r["config_index"] for r in rows] == list(range(len(rows))),
          {"distinct": len(set(cids)), "bad_format": fmt[:5], "collisions": coll[:5]})
    sids = [s["session_id"] for s in sessions]
    exist = sorted(set(sids) & (ex_sids | eng))
    csv_ok, csv_detail = True, None
    try:
        with open(a.sessions_csv) as fh:
            crow = list(csv.DictReader(fh))
        csv_ok = [x["session_id"] for x in crow] == sids and all(int(x["seed"]) == s["seed"] for x, s in zip(crow, sessions))
        csv_detail = len(crow)
    except FileNotFoundError:
        csv_ok, csv_detail = False, "missing"
    R.add("C07_session_ids", len(sids) == len(set(sids)) == 835 and not exist and csv_ok,
          {"distinct": len(set(sids)), "already_existing": exist[:5], "existing_checked": len(ex_sids | eng),
           "expected_sessions_csv_rows": csv_detail, "csv_consistent": csv_ok})
    seeds = [s["seed"] for s in sessions]
    formula = [s["session_id"] for s in sessions if s["seed"] != 920000 + 10 * byid[s["config_id"]]["config_index"] + s["repetition"]
               or s["seed"] != byid[s["config_id"]]["seeds"][s["repetition"] - 1]]
    sc = sorted(set(seeds) & ex_seeds)
    R.add("C08_seeds", len(seeds) == len(set(seeds)) and not formula and not sc,
          {"distinct": len(set(seeds)), "range": [min(seeds), max(seeds)], "formula_mismatch": formula[:5], "collisions": sc[:5],
           "existing_seeds_checked": len(ex_seeds), "existing_range": [min(ex_seeds), max(ex_seeds)]})

    # per-session checks ------------------------------------------------------------------------------------------------
    bparams = baseline_params()
    bad = defaultdict(list)
    s1_prof = defaultdict(Counter)
    comp_spread = Counter()
    tpl = doc["experiment_template"]
    preflight_n = 0
    if not a.no_preflight:
        from src.attack_engine import build_attacks, resolve_semantic_targets
        from src.campaign_scheduler import attack_configs
        from src.config import load_experiment
    tmpd = tempfile.TemporaryDirectory()
    tmp = Path(tmpd.name)
    for s in sessions:
        c = byid.get(s["config_id"])
        if c is None:
            continue
        sid, sc_, k, n, fam = s["session_id"], c["scenario"], c["K"], c["N"], c["attack"]
        try:
            fleet = F.load_fleet(c["fleet_config"], s["seed"])
        except Exception as e:  # noqa: BLE001
            bad["C10"].append((sid, repr(e)))
            continue
        vids = [x.vehicle_id for x in fleet]
        prof = {x.vehicle_id: x.profile[-1] for x in fleet}
        camps, iso = s["campaigns"], s["isolated_attacks"]
        coord = [x for x in camps if x.get("coordinated") is True]
        indep = [x for x in camps if x.get("coordinated") is False]
        coord_t = [v for x in coord for v in x["targets"]]
        ind_t = [v for x in indep for v in x["targets"]]
        iso_t = [x["vehicle"] for x in iso]
        # C09
        if sc_.startswith("S3") and not (2 <= k <= n and k in D.K_LADDER and len(coord_t) == k):
            bad["C09"].append(sid)
        if len(set(coord_t + ind_t + iso_t)) > n:
            bad["C09"].append(sid)
        # C10
        try:
            m = F.members(c, s["seed"], fleet)
        except Exception as e:  # noqa: BLE001
            m = {"coordinated": None, "independent": None, "isolated": None}
            bad["C10"].append((sid, repr(e)))
        allt = coord_t + ind_t + iso_t
        exp_counts = {"S0": (0, 0, 0), "S1": (0, 0, 1), "S2": (0, c["independent_attack_count"], 0),
                      "S3": (k, 0, 0), "S3-probe": (k, 0, 0), "S3-mixed": (k, c["mixed_attack_count"], 0)}[sc_]
        if (m["coordinated"] != coord_t or m["independent"] != ind_t or m["isolated"] != iso_t or len(set(allt)) != len(allt)
                or set(allt) - set(vids) or (len(coord_t), len(ind_t), len(iso_t)) != exp_counts):
            bad["C10"].append(sid)
        # C11
        cnt = Counter(prof.values())
        ks = [sum(prof[v] == p for v in coord_t) for p in "ABC"]
        comp = s["composition"]
        if (max(cnt.values()) - min(cnt.values()) > 1 or [comp["K_A"], comp["K_B"], comp["K_C"]] != ks or sum(ks) != len(coord_t)
                or [comp["N_A"], comp["N_B"], comp["N_C"]] != [cnt.get(p, 0) for p in "ABC"]):
            bad["C11"].append(sid)
        if coord_t and c["member_selection"] == "seeded_profile_balanced_v1":
            caps = [cnt.get(p, 0) for p in "ABC"]
            q = -(-len(coord_t) // 3)                       # ceil(K / 3)
            if all(cp >= q for cp in caps):                 # balance feasible: spread <= 1 required
                if max(ks) - min(ks) > 1:
                    bad["C11"].append((sid, ks))
            else:                                           # a profile has fewer than ceil(K/3) vehicles: it must be exhausted
                short = [i for i in range(3) if caps[i] < q]
                rest = [ks[i] for i in range(3) if i not in short]
                if any(ks[i] != caps[i] for i in short) or (rest and max(rest) - min(rest) > 1):
                    bad["C11"].append((sid, ks, caps))
            comp_spread[max(ks) - min(ks)] += 1
        if sc_ == "S1":
            s1_prof[n][prof[iso_t[0]]] += 1
            if s.get("isolated_target_profile") != prof[iso_t[0]]:
                bad["C11"].append(sid)
        # C18 / C19
        if sc_ == "S2":
            ids = [x["campaign_id"] for x in camps]
            if (coord or len(indep) != c["independent_attack_count"] or len(indep) < 2 or len(set(ids)) != len(ids)
                    or any(len(x["targets"]) != 1 for x in indep) or {x["attack_type"] for x in indep} != {fam}):
                bad["C18"].append(sid)
        if sc_.startswith("S3"):
            ids = [x["campaign_id"] for x in camps]
            if (len(coord) != 1 or len(coord[0]["targets"]) != k or len(set(ids)) != len(ids)
                    or len(indep) != c["mixed_attack_count"] or any(len(x["targets"]) != 1 for x in indep)):
                bad["C19"].append(sid)
        if sc_ in ("S0", "S1") and camps:
            bad["C19"].append((sid, "campaign in S0/S1"))
        # C12 schedule + scenario
        try:
            sched = build_schedule(camps, s["seed"], vids)
            probs = check_scenario(c["scenario_id"], iso, sched)
            if probs:
                bad["C12"].append((sid, probs))
        except Exception as e:  # noqa: BLE001
            bad["C12"].append((sid, repr(e)))
            continue
        if c["scenario"] not in SCEN_PATTERNS or c["temporal_pattern"] not in SCEN_PATTERNS[c["scenario"]]:
            bad["C12"].append((sid, "pattern/scenario"))
        # C13 attack validity and frozen parameters
        if (fam == "none") != (sc_ == "S0") or (fam != "none" and fam not in D.FAMILIES):
            bad["C13"].append((sid, "family"))
        for x in camps:
            T = c.get("member_attack_duration_s", D.ATTACK_S) if x.get("coordinated") else D.ATTACK_S
            key = ("campaign", x["attack_type"], 10.0 if T == 10.0 else 5.0)
            ref = bparams.get(key) or bparams[("campaign", x["attack_type"], 5.0)]
            if x["attack_type"] != fam or x["params"] != ref[0] or (fam != "replay" and float(x.get("duration_s", -1)) != T):
                bad["C13"].append((sid, x["campaign_id"]))
        for x in iso:
            ref = bparams[("isolated", x["type"])]
            p = dict(x["params"])
            refp = dict(ref[0])
            if x["type"] == "replay":
                ok_src = p.pop("source_vehicle") == x["vehicle"]
                refp.pop("source_vehicle")
            else:
                ok_src = True
            if not ok_src or p != refp or x.get("duration_s") != (float(ref[1]) if ref[1] is not None else None) or x["start_s"] != ref[2]:
                bad["C13"].append((sid, x["attack_id"]))
        # C14 timing
        mem = {mm.vehicle_id: mm for cc in sched for mm in cc.members}
        if coord:
            cc = coord[0]
            d = c["member_relative_delays_s"]
            exp = D.delays(c["temporal_pattern"], k)
            got = [(mem[v].start_ns - int(round(D.WARMUP_S * NS))) / NS for v in cc["targets"]]
            if (len(coord) != 1 or cc["start_s"] != D.WARMUP_S or any(abs(x - y) > EPS for x, y in zip(d, exp))
                    or any(abs(x - y) > 1e-9 for x, y in zip(got, exp))):
                bad["C14"].append(sid)
        if sc_ == "S2":
            st = [x["start_s"] for x in indep]
            if st != D.s2_starts(c["independent_attack_count"], c["independent_timing"]):
                bad["C14"].append(sid)
        if sc_ == "S3-mixed":
            st = [x["start_s"] for x in indep]
            if st != [round(D.WARMUP_S + 0.25 + 0.5 * j, 6) for j in range(c["mixed_attack_count"])]:
                bad["C14"].append(sid)
        # C15 bounded spread
        if c["temporal_pattern"] in D.BOUNDED:
            W = D.BOUNDED[c["temporal_pattern"]]
            t = sorted(mem[v].start_ns for v in coord[0]["targets"])
            ideal = [int(round(D.WARMUP_S * NS)) + int(round(W * i / (k - 1) * NS)) for i in range(k)]
            seq_like = any(all(abs((t[i] - t[0]) - st * i * NS) <= 1 for i in range(k)) for st in (0.5, 2.0, 10.0, 5.0))
            if (any(abs(x - y) > 1 for x, y in zip(t, ideal)) or abs((t[-1] - t[0]) - W * NS) > 1 or seq_like
                    or [mem[v].start_ns for v in coord[0]["targets"]] != t):
                bad["C15"].append(sid)
        # C16 partial overlap
        if c["temporal_pattern"] == "partial":
            ms = sorted(sched[0].members, key=lambda mm: mm.start_ns)
            durs = {mm.end_ns - mm.start_ns for mm in ms}
            if fam == "replay":
                durs = {x - int(0.25e6) for x in durs}
            gaps = {ms[i + 1].start_ns - ms[i].start_ns for i in range(len(ms) - 1)}
            ov = {(o["vehicle_a"], o["vehicle_b"]): o["overlap_ns"] for o in pair_overlaps(sched[0])}
            order = [mm.vehicle_id for mm in ms]
            adj = {ov.get((order[i], order[i + 1]), ov.get((order[i + 1], order[i]))) for i in range(len(ms) - 1)}
            two = {ov.get((order[i], order[i + 2]), ov.get((order[i + 2], order[i]))) for i in range(len(ms) - 2)}
            exp_adj = {5 * NS + (int(0.25e6) if fam == "replay" else 0)}
            exp_two = {0 + (int(0.25e6) if fam == "replay" else 0)}
            if durs != {10 * NS} or gaps != {5 * NS} or adj != exp_adj or two != exp_two:
                bad["C16"].append((sid, sorted(durs), sorted(gaps), sorted(adj), sorted(two)))
        # C17 durations
        ends = [mm.end_ns for cc in sched for mm in cc.members]
        starts = [mm.start_ns for cc in sched for mm in cc.members]
        for x in iso:
            st0 = int(round(x["start_s"] * NS))
            starts.append(st0)
            if x["type"] == "replay":
                w = x["params"]["window_s"]
                ends.append(st0 + int(round((w[1] - w[0]) * NS)) + int(0.25e6))
                if w[0] < 0 or w[1] > x["start_s"]:
                    bad["C17"].append((sid, "isolated replay source window"))
            else:
                ends.append(st0 + int(round(x["duration_s"] * NS)))
        dur_ns = int(round(c["duration_s"] * NS))
        if ends and (max(ends) + int(D.TAIL_S * NS) > dur_ns + int(0.25e6) or min(starts) < int(D.WARMUP_S * NS)):
            bad["C17"].append((sid, max(ends) / NS, c["duration_s"]))
        for cc in sched:
            for mm in cc.members:
                if cc.attack_type == "replay":
                    w0, w1 = mm.params["window_ns"]
                    if w0 < 0 or w1 > mm.start_ns or mm.params["source_vehicle"] != mm.vehicle_id or w1 - w0 != mm.end_ns - mm.start_ns - int(0.25e6):
                        bad["C17"].append((sid, "replay source", mm.vehicle_id))
        if sc_ == "S0" and c["duration_s"] != 40.0 or sc_ == "S1" and c["duration_s"] != 20.0:
            bad["C17"].append((sid, "fixed duration"))
        # preflight through the generator's own loaders
        if not a.no_preflight:
            try:
                p = write_experiment(tmp, c, s, tpl)
                cfg = load_experiment(p)
                assert cfg.seed == s["seed"] and len(cfg.fleet) == n and cfg.dataset_version == F.COMPONENT and not cfg.realtime
                rg = cfg.route_generation
                assert rg["method"] == "grid_loops_v1" and Path(rg["network"]) == ROOT / F.ROUTE_GENERATED["network"]
                assert cfg.step_length_s == 0.01 and cfg.output_format == "parquet"
                assert cfg.fleet_info["ecu_jitter"]["fraction"] == 0.01 and cfg.fleet_info["switch_on"]["range_s"] == [0.0, 1.0]
                assert cfg.fleet_info["switch_on"]["resolution_ns"] == 1000 and cfg.fleet_info["switch_on"]["distribution"] == "uniform"
                assert cfg.fleet_info["profile_assignment"]["strategy"] == "balanced_shuffle"
                for pr in cfg.profiles.values():
                    b = pr.bus
                    assert (b.bitrate, b.stuffing, b.queue_limit, b.drop_policy, b.expiry_policy, b.max_frame_age_ms) == \
                        (500000, "exact", 32, "drop_newest", "deadline", 100.0)
                cs = build_schedule(cfg.campaigns, cfg.seed, [x.vehicle_id for x in cfg.fleet])
                assert not check_scenario(cfg.scenario_id, cfg.attacks, cs)
                by_v = {x.vehicle_id: cfg.profiles[x.profile] for x in cfg.fleet}
                build_attacks(resolve_semantic_targets(list(cfg.attacks) + attack_configs(cs), by_v), cfg.fleet, by_v, cfg.seed,
                              campaign_ids={x.campaign_id for x in cs})
                assert [x.vehicle_id for x in cfg.fleet] == vids
                preflight_n += 1
            except Exception as e:  # noqa: BLE001
                bad["C12"].append((sid, "preflight", repr(e)[:200]))
    tmpd.cleanup()

    R.add("C09_K_le_N", not bad["C09"], {"bad": bad["C09"][:5]})
    R.add("C10_member_feasibility", not bad["C10"], {"bad": bad["C10"][:5], "rule_inputs": "seed + frozen fleet only"})
    s1_cov = {n: dict(v) for n, v in sorted(s1_prof.items())}
    R.add("C11_profile_balance", not bad["C11"] and all(len(v) == 3 for v in s1_prof.values()),
          {"bad": bad["C11"][:5], "scaled_campaign_spread_histogram": dict(comp_spread), "S1_target_profiles_per_N": s1_cov,
           "G0": "baseline_first_k (vehicle_01..05, as fleetcan-sim-1.1): composition reported, balance not imposed"})
    R.add("C12_scenario_and_preflight", not bad["C12"] and (a.no_preflight or preflight_n == len(sessions)),
          {"bad": bad["C12"][:5], "preflight_sessions": preflight_n if not a.no_preflight else "skipped"})
    R.add("C13_attack_parameters_frozen", not bad["C13"], {"bad": bad["C13"][:5], "reference": "config/final/matrix (fleetcan-sim-1.1)"})
    R.add("C14_timing", not bad["C14"], {"bad": bad["C14"][:5]})
    nb = sum(1 for r in rows if r["temporal_pattern"] in D.BOUNDED)
    R.add("C15_bounded_spread", not bad["C15"], {"bad": bad["C15"][:5], "configurations": nb, "rule": "t_i = t_0 + W i / (K - 1), t_{K-1} - t_0 = W"})
    npart = sum(1 for r in rows if r["temporal_pattern"] == "partial")
    R.add("C16_partial_overlap", not bad["C16"], {"bad": bad["C16"][:3], "configurations": npart,
                                                   "convention": "half-open [start, end); replay members +0.25 ms"})
    R.add("C17_duration", not bad["C17"], {"bad": bad["C17"][:5]})
    R.add("C18_S2_independent", not bad["C18"], {"bad": bad["C18"][:5]})
    R.add("C19_S3_coordinated", not bad["C19"], {"bad": bad["C19"][:5]})
    R.add("C20_status_frozen", doc.get("matrix_status") == "frozen" and doc.get("matrix_id") == F.MATRIX_ID and doc.get("component") == F.COMPONENT,
          {"matrix_status": doc.get("matrix_status"), "matrix_id": doc.get("matrix_id")})
    R.add("C21_not_approved", doc.get("approved_for_generation") is False, {"approved_for_generation": doc.get("approved_for_generation")})
    refs = subprocess.run(["grep", "-rl", "--include=*.py", "--include=*.sh", "--include=*.yaml", "fleetcan_sim_scale_1_0",
                           "src", "scripts", "analysis", "detection", "config"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    foreign = [r for r in refs if not r.endswith(ALLOWED_REFS)]
    R.add("C22_no_consumer", not foreign, {"referenced_by": foreign, "allowed": list(ALLOWED_REFS)})

    # C23 determinism + manifest
    ybytes, cbytes = F.build()
    det = mbytes == ybytes and Path(a.sessions_csv).exists() and Path(a.sessions_csv).read_bytes() == cbytes
    man_detail = "not checked"
    man_ok = True
    if a.manifest != "none" and Path(a.manifest).exists():
        man = json.loads(Path(a.manifest).read_text())
        man_ok = (man["matrix_sha256"] == hashlib.sha256(mbytes).hexdigest() and man["expected_sessions"] == len(sessions)
                  and man["configurations"] == len(rows) and man["approved_for_generation"] is False and man["matrix_id"] == F.MATRIX_ID
                  and man["expected_sessions_sha256"] == hashlib.sha256(Path(a.sessions_csv).read_bytes()).hexdigest())
        man_detail = {"matrix_sha256": man["matrix_sha256"], "matches": man_ok}
    R.add("C23_identity", det and man_ok, {"rebuild_byte_identical": det, "manifest": man_detail,
                                           "matrix_sha256": hashlib.sha256(mbytes).hexdigest()})
    # C24 consistency with the approved M9 proposal
    prop = yaml.safe_load(F.PROPOSAL.read_text())
    pmain = prop["configurations"]
    main_rows = [r for r in rows if r["purpose"] == "expanded_scaling"]
    keys = ("config_id", "config_index", "scenario", "N", "K", "attack", "temporal_pattern", "independent_timing", "duration_s", "seeds",
            "session_ids", "phase")
    diff = [p["config_id"] for p, r in zip(pmain, main_rows) if any(p.get(k) != r.get(k) for k in keys)]
    mem_ok = True
    with open(D.OUT_DIR / "members_B_balanced.csv") as fh:
        pm = {x["session_id"]: x for x in csv.DictReader(fh)}
    for s in sessions:
        if s["session_id"] in pm:
            x = pm[s["session_id"]]
            coord_t = next((cc["targets"] for cc in s["campaigns"] if cc["coordinated"]), [])
            ind_t = [cc["targets"][0] for cc in s["campaigns"] if not cc["coordinated"]]
            iso_t = [aa["vehicle"] for aa in s["isolated_attacks"]]
            if x["coordinated"].split() != coord_t or x["independent"].split() != ind_t or x["isolated"].split() != iso_t:
                mem_ok = False
    R.add("C24_M9_consistency", len(pmain) == len(main_rows) == 162 and not diff and mem_ok
          and hashlib.sha256(F.PROPOSAL.read_bytes()).hexdigest() == doc["derived_from"]["proposal_sha256"],
          {"main_configurations": len(main_rows), "field_differences": diff[:5], "members_identical_to_M9": mem_ok})
    # C25 frozen code / traffic semantics
    gd = subprocess.run(["git", "diff", "--stat", doc["generator"]["source_commit"], "--", "src", "analysis", "config/profiles", "sumo",
                         "config/final/fleets", "config/m8/fleets", "config/attacks.yaml"], cwd=ROOT, capture_output=True, text=True)
    from src.version import DESCRIPTOR_VERSION, GENERATOR_VERSION, SCHEMA_VERSION
    R.add("C25_frozen_code", gd.returncode == 0 and not gd.stdout.strip() and (GENERATOR_VERSION, SCHEMA_VERSION, DESCRIPTOR_VERSION)
          == (doc["generator"]["version"], doc["generator"]["schema_version"], doc["generator"]["descriptor_version"]) == ("1.3.0", "1.0", "1.0"),
          {"source_commit": doc["generator"]["source_commit"], "diff": gd.stdout.strip()[:300] or "none"})
    need = {"component", "matrix_id", "matrix_sha256", "config_id", "session_id", "repetition", "N", "K", "scenario", "attack",
            "temporal_pattern", "route_source", "coordinated_members", "K_A", "K_B", "K_C"}
    R.add("C26_provenance_declared", need <= set(doc["provenance_tags_required"]), {"missing": sorted(need - set(doc["provenance_tags_required"]))})

    out = {"matrix": a.matrix, "matrix_sha256": hashlib.sha256(mbytes).hexdigest(), "configurations": len(rows), "sessions": len(sessions),
           "approved_for_generation": doc.get("approved_for_generation"), "verdict": "PASS" if R.ok else "FAIL", "checks": R.checks}
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(out, indent=1, default=str))
    for k, v in R.checks.items():
        print(f"{v['status']:4}  {k}")
    print(f"{out['verdict']}: {len(rows)} configurations, {len(sessions)} sessions (approved_for_generation={doc.get('approved_for_generation')})")
    sys.exit(0 if R.ok else 1)


if __name__ == "__main__":
    main()
