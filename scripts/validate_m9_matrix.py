"""Milestone 9: READ-ONLY validator for the proposed expanded-dataset matrix.

Checks configs/m9_expanded_matrix_proposed.yaml without generating anything and without writing to data/:

  M01 approval flag      approved_for_generation is false and no runner/script outside M9 references the matrix
  M02 schema             every configuration has the required fields and a consistent config_id
  M03 scenario/attack    valid scenario, attack family (frozen four; S0 'none'), temporal pattern for the scenario
  M04 K <= N             campaign size K in the frozen ladder, K <= N, K + mixed independents <= N, k_S2 <= N
  M05 repetitions        repetitions == 5 == len(seeds) == len(session_ids)
  M06 unique ids         config_ids, session_ids unique; session_id = <config_id>_R<rep>
  M07 unique seeds       seeds unique within the matrix and follow the declared formula
  M08 duplicates         no two configurations with the same parameter tuple
  M09 baseline/existing  no seed or session_id collides with fleetcan-sim-1.1 (manifest + matrix spec) or any
                         existing session (data/metadata, data/.incomplete) or existing config seed
  M10 timing             delays reproduce the declared pattern (frozen sequential step x i, NEW bounded spread
                         W x i / (K - 1), probe [0, 0.1]); S2 / mixed starts; duration sufficient for every member
                         window + 5 s tail; warm-up 10 s before the first attack
  M11 profile feasibility fleet config for N exists; for every seed the frozen profile assignment yields A/B/C counts
                         differing by <= 1
  M12 membership         member selection (seeded rule) resolves for every session: correct counts, no duplicates,
                         coordinated and independent targets disjoint, K_A/K_B/K_C differ by <= 1, deterministic
                         (resolved twice), equal to results/m9_design/members_B_balanced.csv if present
  M13 scheduler          the campaigns each session WOULD use pass the frozen campaign scheduler validation and
                         scenario definition (src/campaign_scheduler.build_schedule / check_scenario), members end
                         within the session; nothing is executed
  M14 coverage           every attack family occurs in every scenario family that is not replay-only

Usage: .venv/bin/python scripts/validate_m9_matrix.py [--matrix PATH] [--report PATH]
Exit status 0 = all checks pass.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from src.campaign_scheduler import build_schedule, check_scenario  # noqa: E402
import m9_design as D  # noqa: E402

REQUIRED = ("config_id", "config_index", "family", "scenario", "N", "K", "attack", "temporal_pattern", "independent_attack_count",
            "mixed_independent_count", "independent_timing", "duration_s", "repetitions", "seeds", "session_ids", "phase")
SCEN_PATTERNS = {"S0": {"none"}, "S1": {"isolated"}, "S2": {"independent"},
                 "S3": {"simultaneous", "small", "medium", "large", "partial", *D.BOUNDED},
                 "S3-probe": {"probe"}, "S3-mixed": {"small"}}
EPS = 1e-9
REPLAY_OFFSET_S = 0.00025


class Report:
    def __init__(self):
        self.checks = {}

    def add(self, cid, ok, detail):
        self.checks[cid] = {"status": "PASS" if ok else "FAIL", "detail": detail}

    @property
    def ok(self):
        return all(c["status"] == "PASS" for c in self.checks.values())


def existing_ids_and_seeds():
    sids, seeds = set(), set()
    base = ROOT / "data" / "final" / "fleetcan-sim-1.1" / "manifest.jsonl"
    for line in base.read_text().splitlines():
        r = json.loads(line)
        sids.add(r["session_id"])
        for k in ("seed", "random_seed"):
            if isinstance(r.get(k), int):
                seeds.add(r[k])
    spec = ROOT / "config" / "final" / "matrix" / "matrix_spec.json"
    for r in json.loads(spec.read_text())["runs"]:
        sids.add(r["session_id"])
        seeds.add(int(r["seed"]))
    n_meta = 0
    for f in glob.glob(str(ROOT / "data" / "metadata" / "*" / "metadata.json")):
        d = json.loads(Path(f).read_text())
        sids.add(d.get("session_id") or Path(f).parent.name)
        if isinstance(d.get("random_seed"), int):
            seeds.add(d["random_seed"])
        n_meta += 1
    for d in (ROOT / "data").glob("*/*"):
        if d.is_dir() and d.parent.name in ("raw", "ground_truth", "validation", "analysis", ".incomplete"):
            sids.add(d.name)
    cfg_seeds = set()
    for f in glob.glob(str(ROOT / "config" / "**" / "*.yaml"), recursive=True):
        for m in re.finditer(r"^\s*seed:\s*(\d+)\s*$", Path(f).read_text(), re.M):
            cfg_seeds.add(int(m.group(1)))
    return sids, seeds | cfg_seeds, n_meta


def would_be_campaigns(c, seed, members):
    """Campaign / attack configuration a future builder would emit (frozen M6 conventions); NOT written anywhere."""
    f, start, atk = c["attack"], D.WARMUP_S, D.attack_seconds(c["temporal_pattern"])

    def body(cid, targets, s, delays, coordinated):
        out = {"campaign_id": cid, "attack_type": f, "coordinated": coordinated, "targets": targets, "start_s": s}
        if not coordinated or c["temporal_pattern"] == "simultaneous":
            out["delay_pattern"] = "simultaneous"
        else:
            out["delay_pattern"], out["relative_delays_s"] = "fixed", delays
        if f == "replay":
            out["params"] = {"window_relative_s": [-atk, 0.0], "source_vehicle": "self", "time_offset_ms": 0.25}
        else:
            out["duration_s"], out["params"] = atk, D.PARAMS[f]
        return out

    camps, iso = [], []
    if c["scenario"] == "S1":
        iso = [{"attack_id": f"A_S1_{f.upper()}", "type": f, "vehicle": members["isolated"][0], "start_s": 10.0}]
    elif c["scenario"] == "S2":
        for j, (v, s) in enumerate(zip(members["independent"], c["independent_start_s"])):
            camps.append(body(f"C_S2_{f.upper()}_{j + 1}", [v], s, [0.0], False))
    elif c["scenario"] != "S0":
        camps.append(body("C_MAIN", members["coordinated"], start, c["member_relative_delays_s"], True))
        for j, (v, s) in enumerate(zip(members["independent"], c.get("independent_start_s") or [])):
            camps.append(body(f"C_IND_{j + 1}", [v], s, [0.0], False))
    return camps, iso


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=str(D.OUT_YAML))
    ap.add_argument("--report", default=None, help="optional JSON report path (outside data/)")
    a = ap.parse_args()
    if a.report and Path(a.report).resolve().is_relative_to((ROOT / "data").resolve()):
        sys.exit("refusing to write into data/")
    doc = yaml.safe_load(Path(a.matrix).read_text())
    rows = doc["configurations"]
    R = Report()

    # M01
    refs = subprocess.run(["grep", "-rl", "--include=*.py", "--include=*.sh", "--include=*.yaml", "m9_expanded_matrix_proposed",
                           "src", "scripts", "analysis", "detection", "config"],
                          cwd=ROOT, capture_output=True, text=True).stdout.split()
    foreign = [r for r in refs if not re.search(r"(m9_design|validate_m9_matrix|m91_freeze)\.py$", r)]
    R.add("M01_not_approved", doc.get("approved_for_generation") is False and not foreign,
          {"approved_for_generation": doc.get("approved_for_generation"), "referenced_by": foreign})

    # M02
    bad = []
    for r in rows:
        miss = [k for k in REQUIRED if k not in r]
        try:
            exp = D.cfg(r["family"], r["scenario"], r["N"], r["attack"], r["temporal_pattern"], r["K"],
                        r["independent_attack_count"] or r["mixed_independent_count"], r["independent_timing"])
        except Exception as e:  # noqa: BLE001
            exp = {"config_id": repr(e), "duration_s": float("nan")}
        if miss or exp["config_id"] != r["config_id"] or not abs(exp["duration_s"] - r["duration_s"]) <= EPS:
            bad.append({"config_id": r.get("config_id"), "missing": miss, "expected_id": exp["config_id"]})
    R.add("M02_schema", not bad, {"configurations": len(rows), "bad": bad[:10]})

    # M03
    bad = [r["config_id"] for r in rows
           if r["scenario"] not in SCEN_PATTERNS or r["temporal_pattern"] not in SCEN_PATTERNS[r["scenario"]]
           or (r["attack"] == "none") != (r["scenario"] == "S0") or (r["attack"] != "none" and r["attack"] not in D.FAMILIES)
           or (r["scenario"] == "S3-probe" and r["attack"] != "replay")]
    R.add("M03_scenario_attack_pattern", not bad, {"bad": bad})

    # M04
    bad = []
    for r in rows:
        n, k = r["N"], r["K"]
        if n not in D.NEW_N:
            bad.append((r["config_id"], "N not a new fleet size"))
        if r["scenario"] in ("S3", "S3-probe", "S3-mixed") and (k not in D.K_LADDER or k > n or k < 2):
            bad.append((r["config_id"], "K"))
        if r["scenario"] in ("S0", "S1", "S2") and k != 0:
            bad.append((r["config_id"], "K must be 0 outside S3"))
        if k + r["mixed_independent_count"] > n or r["independent_attack_count"] > n:
            bad.append((r["config_id"], "too many attacked vehicles"))
        if r["scenario"] == "S2" and r["independent_attack_count"] < 2:
            bad.append((r["config_id"], "S2 needs >= 2"))
    R.add("M04_K_le_N", not bad, {"bad": bad, "penetration_rho": sorted({round(r['K'] / r['N'], 3) for r in rows if r['K']})})

    # M05
    bad = [r["config_id"] for r in rows if not (r["repetitions"] == D.REPS == len(r["seeds"]) == len(r["session_ids"]))]
    R.add("M05_repetitions", not bad, {"repetitions": D.REPS, "bad": bad})

    # M06
    cids = [r["config_id"] for r in rows]
    sids = [s for r in rows for s in r["session_ids"]]
    form = [s for r in rows for i, s in enumerate(r["session_ids"], 1) if s != f"{r['config_id']}_R{i}"]
    R.add("M06_unique_ids", len(cids) == len(set(cids)) and len(sids) == len(set(sids)) and not form
          and all(s.startswith(D.SESSION_PREFIX) for s in sids),
          {"config_ids": len(cids), "distinct": len(set(cids)), "session_ids": len(sids), "distinct_sessions": len(set(sids)),
           "bad_form": form[:5]})

    # M07
    seeds = [s for r in rows for s in r["seeds"]]
    formula = [r["config_id"] for r in rows if r["seeds"] != D.seeds_for(r["config_index"])]
    idx = [r["config_index"] for r in rows]
    R.add("M07_unique_seeds", len(seeds) == len(set(seeds)) and not formula and idx == list(range(len(rows))),
          {"seeds": len(seeds), "distinct": len(set(seeds)), "range": [min(seeds), max(seeds)], "formula_mismatch": formula})

    # M08
    key = lambda r: (r["scenario"], r["N"], r["K"], r["attack"], r["temporal_pattern"], r["independent_attack_count"],
                     r["mixed_independent_count"], r["independent_timing"], r["duration_s"])
    dup = [k for k, v in Counter(map(key, rows)).items() if v > 1]
    R.add("M08_no_duplicate_configs", not dup, {"duplicates": dup})

    # M09
    ex_sids, ex_seeds, n_meta = existing_ids_and_seeds()
    sid_hit = sorted(set(sids) & ex_sids)
    seed_hit = sorted(set(seeds) & ex_seeds)
    R.add("M09_no_collision", not sid_hit and not seed_hit,
          {"existing_session_ids": len(ex_sids), "existing_metadata_sessions": n_meta, "existing_seeds": len(ex_seeds),
           "existing_seed_range": [min(ex_seeds), max(ex_seeds)], "session_collisions": sid_hit[:5], "seed_collisions": seed_hit[:5]})

    # M10
    bad = []
    for r in rows:
        sc, p, k = r["scenario"], r["temporal_pattern"], r["K"]
        tail_end = []
        if sc in ("S3", "S3-probe", "S3-mixed"):
            d = r["member_relative_delays_s"]
            if len(d) != k or d != sorted(d) or d[0] != 0.0:
                bad.append((r["config_id"], "delay vector"))
            if p in D.SEQUENTIAL and any(abs(x - D.SEQUENTIAL[p] * i) > EPS for i, x in enumerate(d)):
                bad.append((r["config_id"], "sequential step"))
            if p in D.BOUNDED and (abs(d[-1] - D.BOUNDED[p]) > EPS or any(abs(x - D.BOUNDED[p] * i / (k - 1)) > EPS for i, x in enumerate(d))):
                bad.append((r["config_id"], "bounded spread"))
            if r.get("member_attack_duration_s") != D.attack_seconds(p):
                bad.append((r["config_id"], "attack duration"))
            tail_end += [D.WARMUP_S + x + r["member_attack_duration_s"] for x in d]
        if sc in ("S2", "S3-mixed"):
            st = r["independent_start_s"]
            m = r["independent_attack_count"] or r["mixed_independent_count"]
            if len(st) != m or st != sorted(st) or min(st) < D.WARMUP_S:
                bad.append((r["config_id"], "independent starts"))
            if sc == "S2" and r["independent_timing"] == "near_simultaneous" and abs(st[-1] - st[0] - 0.5) > EPS:
                bad.append((r["config_id"], "near-simultaneous spread != 0.5 s"))
            tail_end += [s + D.ATTACK_S for s in st]
        if sc == "S1":
            tail_end = [D.WARMUP_S + D.ATTACK_S]
        if sc == "S0" and r["duration_s"] != 40.0:
            bad.append((r["config_id"], "S0 duration"))
        if tail_end and r["duration_s"] + EPS < max(tail_end) + D.TAIL_S:
            bad.append((r["config_id"], f"duration {r['duration_s']} < last end {max(tail_end)} + tail"))
    R.add("M10_timing_and_duration", not bad, {"bad": bad[:10], "duration_range_s": [min(r["duration_s"] for r in rows), max(r["duration_s"] for r in rows)]})

    # M11 + M12 + M13
    fleets, prof_bad, mem_bad, sched_bad = {}, [], [], []
    stored = {}
    mfile = D.OUT_DIR / "members_B_balanced.csv"
    if mfile.exists():
        with open(mfile) as fh:
            stored = {x["session_id"]: x for x in csv.DictReader(fh)}
    comp = Counter()
    for r in rows:
        fpath = ROOT / "config" / "m8" / "fleets" / f"fleet_{r['N']}.yaml"
        if not fpath.exists():
            prof_bad.append((r["config_id"], "no fleet config"))
            continue
        for sid, seed in zip(r["session_ids"], r["seeds"]):
            fleet = D.fleet_for(r["N"], seed)
            cnt = Counter(a.profile for a in fleet)
            if len(fleet) != r["N"] or len(cnt) != 3 or max(cnt.values()) - min(cnt.values()) > 1:
                prof_bad.append((sid, dict(cnt)))
            try:
                m1, m2 = D.resolve_session(r, seed), D.resolve_session(r, seed)
            except Exception as e:  # noqa: BLE001
                mem_bad.append((sid, repr(e)))
                continue
            want = {"coordinated": r["K"] if r["scenario"] in ("S3", "S3-probe", "S3-mixed") else 0,
                    "independent": r["independent_attack_count"] if r["scenario"] == "S2" else r["mixed_independent_count"],
                    "isolated": 1 if r["scenario"] == "S1" else 0}
            allv = m1["coordinated"] + m1["independent"] + m1["isolated"]
            ks = [m1["K_A"], m1["K_B"], m1["K_C"]]
            if (m1 != m2 or any(len(m1[g]) != want[g] for g in want) or len(set(allv)) != len(allv)
                    or set(allv) - {a.vehicle_id for a in fleet} or (want["coordinated"] and max(ks) - min(ks) > 1)
                    or sum(ks) != want["coordinated"]):
                mem_bad.append(sid)
            if stored:
                s = stored.get(sid)
                if s is None or s["coordinated"].split() != m1["coordinated"] or s["independent"].split() != m1["independent"] \
                        or s["isolated"].split() != m1["isolated"] or int(s["seed"]) != seed:
                    mem_bad.append((sid, "members csv mismatch"))
            if want["coordinated"]:
                comp[tuple(ks)] += 1
            try:
                camps, iso = would_be_campaigns(r, seed, m1)
                sched = build_schedule(camps, seed, [a.vehicle_id for a in fleet])
                errs = check_scenario(r["scenario"].split("-")[0], iso, sched)
                end = max([m.end_ns for c in sched for m in c.members] + [0]) / 1e9
                # replay members end time_offset (0.25 ms) after the nominal attack end; the frozen M6 duration rule
                # (last start + attack + 5 s) does not add it, so the tail is 5 s - 0.25 ms there (identical here)
                if errs or end + D.TAIL_S > r["duration_s"] + REPLAY_OFFSET_S + EPS:
                    sched_bad.append((sid, errs, end))
                if r["scenario"].startswith("S3"):
                    main_c = [c for c in sched if c.campaign_id == "C_MAIN"][0]
                    rel = [m.relative_delay_ns / 1e9 for m in main_c.members] if hasattr(main_c.members[0], "relative_delay_ns") else None
                    if rel is not None and any(abs(x - y) > 1e-9 for x, y in zip(rel, r["member_relative_delays_s"])):
                        sched_bad.append((sid, "scheduler delays differ"))
            except Exception as e:  # noqa: BLE001
                sched_bad.append((sid, repr(e)))
    R.add("M11_profile_feasibility", not prof_bad, {"sessions": len(sids), "bad": prof_bad[:5]})
    R.add("M12_membership_feasibility", not mem_bad,
          {"sessions": len(sids), "members_csv": mfile.name if stored else None, "bad": mem_bad[:5],
           "K_A_K_B_K_C_spread_max": max((max(k) - min(k) for k in comp), default=0)})
    R.add("M13_scheduler_compatibility", not sched_bad, {"sessions": len(sids), "bad": sched_bad[:5]})

    # M14
    cov = defaultdict(set)
    for r in rows:
        if r["attack"] != "none":
            cov[r["family"]].add(r["attack"])
    gaps = {f: sorted(set(D.FAMILIES) - v) for f, v in cov.items() if f != "S3_probe_replay" and set(D.FAMILIES) - v}
    R.add("M14_attack_coverage", not gaps, {"gaps": gaps})

    out = {"matrix": str(Path(a.matrix).relative_to(ROOT)) if Path(a.matrix).resolve().is_relative_to(ROOT) else a.matrix,
           "approved_for_generation": doc.get("approved_for_generation"), "configurations": len(rows), "sessions": len(sids),
           "verdict": "PASS" if R.ok else "FAIL", "checks": R.checks}
    text = json.dumps(out, indent=1, default=str)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(text)
    for k, v in R.checks.items():
        print(f"{v['status']:4}  {k}")
    print(f"{out['verdict']}: {len(rows)} configurations, {len(sids)} sessions (approved_for_generation={doc.get('approved_for_generation')})")
    sys.exit(0 if R.ok else 1)


if __name__ == "__main__":
    main()
