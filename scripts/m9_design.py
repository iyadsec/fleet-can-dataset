"""Milestone 9: expanded-dataset experimental design (DESIGN ONLY — generates no CAN data, writes no sessions).

Defines three candidate designs (A minimal, B balanced, C comprehensive) for the NEW fleet sizes N = 20 / 50 / 100
(the frozen fleetcan-sim-1.1 baseline already covers N = 2 / 5 / 10 and is reused unchanged), estimates their cost
from MEASURED M8 / M8.1 generator performance, and writes the PROPOSED matrix of the recommended design to
configs/m9_expanded_matrix_proposed.yaml with approved_for_generation: false.

Nothing here uses detector output. Selection rules use only the experiment seed and the frozen fleet configuration.

Usage: .venv/bin/python scripts/m9_design.py [--write]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import build_fleet, derive_seed  # noqa: E402

OUT_YAML = ROOT / "configs" / "m9_expanded_matrix_proposed.yaml"
OUT_DIR = ROOT / "results" / "m9_design"
FAMILIES = ("flooding", "fuzzy", "malfunction", "replay")
NEW_N = (20, 50, 100)
K_LADDER = (2, 5, 10, 20, 50, 100)
SEED_BASE = 920_000                       # baseline 610001-611215, M8/M8.1 tests 80010-83020: no overlap
SESSION_PREFIX = "FCS10_"                 # component fleetcan-sim-scale-1.0 (baseline: FC11_)
REPS = 5
WARMUP_S, TAIL_S, ATTACK_S, PARTIAL_ATTACK_S = 10.0, 5.0, 5.0, 10.0

# Frozen attack parameters (docs/final_protocol.md section 3; identical to scripts/m6_build_matrix.py)
PARAMS = {
    "flooding": {"can_id": 0x000, "rate_hz": 1600, "dlc": 8, "payload": "zeros"},
    "fuzzy": {"mode": "both", "rate_hz": 1000, "id_range": [0x000, 0x7FF], "dlc": "random"},
    "malfunction": {"mode": "inject", "target": "vehicle_speed", "manipulation": {"strategy": "constant", "value": 250.0}, "rate_hz": 100},
    "replay": {"window_relative_s": "[-attack_s, 0.0]", "source_vehicle": "self", "time_offset_ms": 0.25},
}
# Temporal patterns. Sequential = frozen baseline semantics (scripts/m6_build_matrix.py): member i (0-based, in the
# configured member order) starts at campaign_start + i * step. bounded_spread_* are NEW expanded-dataset conditions.
SEQUENTIAL = {"simultaneous": 0.0, "small": 0.5, "medium": 2.0, "large": 10.0, "partial": 5.0, "probe": 0.1}
BOUNDED = {"bounded_spread_0p5s": 0.5, "bounded_spread_2s": 2.0, "bounded_spread_10s": 10.0}

# ------------------------------------------------------------------ measured cost model (M8 / M8.1, generator 1.3.0)
COST = {
    "benign_frames_per_vehicle_s": 320.0,        # measured 314-323 (S0, N = 10..100)
    "attack_frames_per_member": {"flooding": 1600, "fuzzy": 1000, "malfunction": 100, "replay": 324},   # per attack second
    "gen_s_fixed": 2.5, "gen_us_per_frame": 127.0,          # measured 122-137 us/frame for N >= 20
    "analysis_s_fixed": 0.5, "analysis_us_per_frame": 17.5,  # measured 16-20 us/frame
    "mb_per_mframe": 52.0,                                   # measured 48-64 MB per 1e6 frames (all outputs)
    "replay_extra_mb_per_mframe": 55.0,                      # replay frames also carry a replay_map row (fitted, 3 sessions)
    "descriptors_per_frame": 0.0199,
}


def delays(pattern: str, k: int) -> list[float]:
    if pattern in SEQUENTIAL:
        if pattern == "probe":
            return [0.0, 0.1][:k]
        return [round(SEQUENTIAL[pattern] * i, 6) for i in range(k)]
    if pattern in BOUNDED:
        w = BOUNDED[pattern]
        return [round(w * i / (k - 1), 9) if k > 1 else 0.0 for i in range(k)]
    raise ValueError(pattern)


def attack_seconds(pattern: str) -> float:
    return PARTIAL_ATTACK_S if pattern == "partial" else ATTACK_S


# ------------------------------------------------------------------ configuration families
def cfg(family, scenario, n, attack, pattern="none", k=0, n_ind=0, ind_timing="none", note=""):
    if scenario == "S0":
        dur = 40.0
    elif scenario == "S1":
        dur = 20.0
    elif scenario == "S2":
        starts = s2_starts(n_ind, ind_timing)
        dur = starts[-1] + ATTACK_S + TAIL_S
    elif scenario == "S3-mixed":
        d = delays(pattern, k)
        dur = WARMUP_S + max(d[-1], 0.25 + 0.5 * (n_ind - 1)) + ATTACK_S + TAIL_S
    else:
        dur = WARMUP_S + delays(pattern, k)[-1] + attack_seconds(pattern) + TAIL_S
    kk = f"K{k:03d}" if k else "K000"
    ii = f"_I{n_ind:02d}{ind_timing[:4]}" if n_ind and scenario != "S1" else ""
    cid = f"{SESSION_PREFIX}N{n:03d}_{scenario}_{attack}_{kk}_{pattern}{ii}"
    return {"config_id": cid, "family": family, "scenario": scenario, "N": n, "K": k, "attack": attack, "temporal_pattern": pattern,
            "independent_attack_count": n_ind if scenario in ("S1", "S2") else 0, "mixed_independent_count": n_ind if scenario == "S3-mixed" else 0,
            "independent_timing": ind_timing, "duration_s": round(dur, 6), "repetitions": REPS, "note": note}


def s2_starts(k: int, timing: str) -> list[float]:
    if timing == "near_simultaneous":            # frozen baseline S2: starts evenly spread over [10.0, 10.5] s
        return [round(WARMUP_S + 0.5 * j / (k - 1), 6) for j in range(k)]
    if timing == "staggered_0.5s":               # NEW: independent mirror of the coordinated 0.5 s sequential stagger
        return [round(WARMUP_S + 0.5 * j, 6) for j in range(k)]
    if timing == "separated_10s":                # NEW: independent mirror of the coordinated 10 s sequential stagger
        return [round(WARMUP_S + 10.0 * j, 6) for j in range(k)]
    raise ValueError(timing)


def latin(i: int, width: int = 2) -> list[str]:
    """Balanced reduced-factorial attack assignment: `width` consecutive families starting at index i (mod 4)."""
    return [FAMILIES[(i + j) % 4] for j in range(width)]


K_INDEX = {k: i for i, k in enumerate(K_LADDER)}


def rot(n: int, k: int, extra: int = 0) -> int:
    """Rotation index of an (N, K) cell: consecutive cells get different attack pairs, so every family occurs
    equally often (+-1) per N, per K and per temporal pattern (checked in results/m9_design/coverage)."""
    return NEW_N.index(n) + K_INDEX[k] + extra


def k_values(n: int) -> list[int]:
    return [k for k in K_LADDER if k <= n]


ALL4_K = (5,)             # K values with all four families at every N (plus K = N, the full-fleet campaign)
STAGGER_K = (2, 5, 10, 20)
LADDER_K = 5              # temporal ladder fixed at K = 5 (baseline has N = 5 and N = 10 at K = 5)
S2_K = (5, 10)
MIXED = {20: (5, 2), 50: (10, 5), 100: (10, 5)}


def design_B() -> list[dict]:
    c = []
    for n in NEW_N:
        c.append(cfg("S0_scale", "S0", n, "none", note="benign fleet control at each new scale"))
        for f in FAMILIES:
            c.append(cfg("S1_isolated", "S1", n, f, "isolated", 0, 1, "isolated_start_10s", note="one seeded vehicle; single incident in a large fleet"))
    for n in NEW_N:
        for k in S2_K:
            for f in (FAMILIES if k == 5 else latin(rot(n, k, 1))):   # canonical hard negative; all families at k = 5
                c.append(cfg("S2_hardneg_near_simultaneous", "S2", n, f, "independent", 0, k, "near_simultaneous",
                             "k independent attacks, starts within 0.5 s (baseline S2 semantics)"))
            for f in latin(rot(n, k)):                            # same families as the coordinated 'small' cell (N, K)
                c.append(cfg("S2_hardneg_staggered", "S2", n, f, "independent", 0, k, "staggered_0.5s",
                             "k independent attacks at 0.5 s steps: timing identical to coordinated 'small', coordinated=false"))
        for f in latin(rot(n, LADDER_K, 3)):                       # same families as the coordinated 'large' K = 5 cell
            c.append(cfg("S2_hardneg_separated", "S2", n, f, "independent", 0, LADDER_K, "separated_10s",
                         "5 independent attacks at 10 s steps (no temporal overlap): timing identical to coordinated 'large' K = 5"))
    for n in NEW_N:
        for k in k_values(n):
            fams = FAMILIES if (k in ALL4_K or k == n) else latin(rot(n, k))
            for f in fams:
                c.append(cfg("S3_NK_grid_simultaneous", "S3", n, f, "simultaneous", k, note="N x K core grid"))
    for n in NEW_N:
        for k in STAGGER_K:
            for f in latin(rot(n, k)):
                c.append(cfg("S3_NK_grid_small_stagger", "S3", n, f, "small", k, note="0.5 s sequential stagger (frozen semantics)"))
    for n in NEW_N:
        for pi, pat in enumerate(("medium", "large", "partial")):
            for f in latin(rot(n, LADDER_K, 2 * pi + 1)):
                c.append(cfg("S3_temporal_ladder_K5", "S3", n, f, pat, LADDER_K,
                             note="frozen sequential timing ladder at fixed K = 5 (extends baseline N = 5 / 10, K = 5)"))
    cells = [(n, k, pat) for n in NEW_N for k in (20, 50, 100) if k <= n for pat in BOUNDED]
    for i, (n, k, pat) in enumerate(cells):            # 12 cells, one family each, each family exactly 3 times
        for f in (FAMILIES[i % 4],):
                    c.append(cfg("S3_bounded_spread_largeK", "S3", n, f, pat, k,
                                 note="NEW condition: K members evenly spread over a fixed window W"))
    for n in NEW_N:
        c.append(cfg("S3_probe_replay", "S3-probe", n, "replay", "probe", 2, note="0.1 s temporal-resolution probe"))
    for n in NEW_N:
        k, m = MIXED[n]
        for f in latin(2 * NEW_N.index(n)):                       # 2 families per N, all four across N = 20 / 50
            c.append(cfg("S3_mixed", "S3-mixed", n, f, "small", k, m, "mixed_offsets",
                         "coordinated K (0.5 s) + m same-family independent attacks at 10.25 + 0.5 j s (baseline mixed semantics)"))
    for x in c:
        x["phase"] = PHASE[x["family"]]
    return c


PHASE = {f: 1 for f in ("S0_scale", "S1_isolated", "S2_hardneg_near_simultaneous", "S3_NK_grid_simultaneous", "S3_probe_replay", "S3_mixed")}
PHASE.update({f: 2 for f in ("S3_NK_grid_small_stagger", "S2_hardneg_staggered", "S2_hardneg_separated", "S3_temporal_ladder_K5", "S3_bounded_spread_largeK")})


def design_A() -> list[dict]:
    c = []
    for n in NEW_N:
        c.append(cfg("S0_scale", "S0", n, "none"))
    for f in FAMILIES:
        c.append(cfg("S1_isolated", "S1", 100, f, "isolated", 0, 1, "isolated_start_10s"))
    for n in NEW_N:
        for f in ("flooding", "replay"):
            c.append(cfg("S2_hardneg_near_simultaneous", "S2", n, f, "independent", 0, 5, "near_simultaneous"))
    pairs = sorted({(100, k) for k in K_LADDER} | {(n, 5) for n in NEW_N})
    for n, k in pairs:
        for f in ("flooding", "replay"):
            c.append(cfg("S3_NK_ladders_simultaneous", "S3", n, f, "simultaneous", k))
    for pat in ("small", "medium", "large", "partial"):
        for f in ("flooding", "replay"):
            c.append(cfg("S3_temporal_ladder_K5", "S3", 100, f, pat, 5))
    c.append(cfg("S3_probe_replay", "S3-probe", 100, "replay", "probe", 2))
    c.append(cfg("S3_mixed", "S3-mixed", 100, "replay", "small", 10, 5, "mixed_offsets"))
    return c


def design_C() -> list[dict]:
    c = []
    for n in NEW_N:
        c.append(cfg("S0_scale", "S0", n, "none"))
        for f in FAMILIES:
            c.append(cfg("S1_isolated", "S1", n, f, "isolated", 0, 1, "isolated_start_10s"))
        for k in [k for k in (2, 5, 10, 20, 50) if k < n]:
            for t in ("near_simultaneous", "staggered_0.5s"):
                for f in FAMILIES:
                    c.append(cfg("S2_hardneg", "S2", n, f, "independent", 0, k, t))
        for k in k_values(n):
            for pat in ("simultaneous", "small", "medium", "large", "partial") + tuple(BOUNDED if k >= 20 else ()):
                for f in FAMILIES:
                    c.append(cfg("S3_full_factorial", "S3", n, f, pat, k))
        c.append(cfg("S3_probe_replay", "S3-probe", n, "replay", "probe", 2))
        for k, m in [(k, max(1, k // 2)) for k in (5, 10, 20) if k + max(1, k // 2) <= n]:
            for f in FAMILIES:
                c.append(cfg("S3_mixed", "S3-mixed", n, f, "small", k, m, "mixed_offsets"))
    return c


# ------------------------------------------------------------------ cost
def members(c) -> int:
    if c["scenario"] == "S0":
        return 0
    if c["scenario"] == "S1":
        return 1
    if c["scenario"] == "S2":
        return c["independent_attack_count"]
    return c["K"] + c["mixed_independent_count"]


def session_cost(c) -> dict:
    frames = COST["benign_frames_per_vehicle_s"] * c["N"] * c["duration_s"]
    extra_mb = 0.0
    if c["attack"] != "none":
        af = members(c) * COST["attack_frames_per_member"][c["attack"]] * attack_seconds(c["temporal_pattern"])
        frames += af
        if c["attack"] == "replay":
            extra_mb = af / 1e6 * COST["replay_extra_mb_per_mframe"]
    return {"frames": frames, "descriptors": frames * COST["descriptors_per_frame"], "mb": frames / 1e6 * COST["mb_per_mframe"] + extra_mb,
            "gen_s": COST["gen_s_fixed"] + COST["gen_us_per_frame"] * frames / 1e6,
            "analysis_s": COST["analysis_s_fixed"] + COST["analysis_us_per_frame"] * frames / 1e6,
            "sim_s": c["duration_s"], "vehicle_s": c["N"] * c["duration_s"]}


def summarize(name: str, configs: list[dict]) -> dict:
    tot = Counter()
    per_n, per_scen = Counter(), Counter()
    longest = max(configs, key=lambda c: c["N"] * c["duration_s"])
    for c in configs:
        s = session_cost(c)
        for k, v in s.items():
            tot[k] += v * c["repetitions"]
        per_n[c["N"]] += s["mb"] * c["repetitions"] / 1e3
        per_scen[c["scenario"]] += s["mb"] * c["repetitions"] / 1e3
    per_phase = Counter()
    for c in configs:
        if "phase" in c:
            per_phase[c["phase"]] += session_cost(c)["mb"] * c["repetitions"] / 1e3
    ids = [c["config_id"] for c in configs]
    assert len(ids) == len(set(ids)), f"{name}: duplicate config ids"
    return {"design": name, "configurations": len(configs), "sessions": sum(c["repetitions"] for c in configs),
            "simulated_hours": round(tot["sim_s"] / 3600, 2), "vehicle_hours": round(tot["vehicle_s"] / 3600, 1),
            "frames_million": round(tot["frames"] / 1e6, 1), "descriptors_million": round(tot["descriptors"] / 1e6, 2),
            "storage_gb": round(tot["mb"] / 1e3, 1), "generation_hours": round((tot["gen_s"] + tot["analysis_s"]) / 3600, 1),
            "storage_gb_per_N": {n: round(v, 1) for n, v in sorted(per_n.items())},
            "storage_gb_per_scenario": {k: round(v, 2) for k, v in sorted(per_scen.items())},
            "storage_gb_per_phase": {k: round(v, 1) for k, v in sorted(per_phase.items())},
            "longest_session": f"{longest['config_id']} ({longest['N']} vehicles x {longest['duration_s']} s)"}


# ------------------------------------------------------------------ deterministic member selection (proposal)
def fleet_for(n: int, seed: int):
    import yaml
    sec = yaml.safe_load((ROOT / "config" / "m8" / "fleets" / f"fleet_{n}.yaml").read_text())["fleet"]
    return build_fleet(sec, {p: None for p in ("profile_A", "profile_B", "profile_C")}, seed)[0]


def stratified_members(fleet, k: int, rng, exclude=()) -> list[str]:
    """Profile-balanced seeded selection of k vehicles.

    1. quotas: floor(k / P) per profile; the k mod P extra places go to profiles in a seeded random order
       (so no profile is systematically favoured at small k, e.g. K = 1 or 2); a quota above a profile's eligible
       vehicles is capped and the excess moved to the next profile in the same seeded order (water filling);
    2. within each profile: seeded choice without replacement from the sorted vehicle IDs;
    3. member order (= delay rank for staggered patterns): seeded permutation of the chosen vehicles,
       so delay rank is independent of profile and vehicle ID.
    Result: K_A, K_B, K_C differ by at most 1 whenever every profile has enough eligible vehicles."""
    excl = set(exclude)
    pool = [a for a in fleet if a.vehicle_id not in excl]
    if k > len(pool):
        raise ValueError(f"need {k} vehicles, {len(pool)} eligible")
    by_p = {}
    for a in pool:
        by_p.setdefault(a.profile, []).append(a.vehicle_id)
    profs = sorted(by_p)
    order = [profs[i] for i in rng.permutation(len(profs))]
    quota = {p: k // len(profs) for p in profs}
    for p in order[: k % len(profs)]:
        quota[p] += 1
    while any(quota[p] > len(by_p[p]) for p in profs):
        excess = 0
        for p in profs:
            if quota[p] > len(by_p[p]):
                excess += quota[p] - len(by_p[p])
                quota[p] = len(by_p[p])
        for p in order:
            take = min(excess, len(by_p[p]) - quota[p])
            quota[p] += take
            excess -= take
    chosen = []
    for p in profs:
        if quota[p]:
            chosen += [str(v) for v in rng.choice(sorted(by_p[p]), size=quota[p], replace=False)]
    return [chosen[i] for i in rng.permutation(len(chosen))]


def resolve_session(c: dict, seed: int) -> dict:
    """Targets of one session from its seed only (independent streams per purpose; frozen streams untouched)."""
    fleet = fleet_for(c["N"], seed)
    prof = {a.vehicle_id: a.profile[-1] for a in fleet}
    out = {"coordinated": [], "independent": [], "isolated": []}
    if c["scenario"] == "S1":
        rng = np.random.default_rng(derive_seed(seed, "isolated_target"))
        out["isolated"] = stratified_members(fleet, 1, rng)
    elif c["scenario"] == "S2":
        rng = np.random.default_rng(derive_seed(seed, "independent_targets"))
        out["independent"] = stratified_members(fleet, c["independent_attack_count"], rng)
    elif c["scenario"] in ("S3", "S3-probe", "S3-mixed"):
        rng = np.random.default_rng(derive_seed(seed, "campaign_members/C_MAIN"))
        out["coordinated"] = stratified_members(fleet, c["K"], rng)
        if c["mixed_independent_count"]:
            rng2 = np.random.default_rng(derive_seed(seed, "independent_targets"))
            out["independent"] = stratified_members(fleet, c["mixed_independent_count"], rng2, exclude=out["coordinated"])
    comp = {f"K_{p}": sum(prof[v] == p for v in out["coordinated"]) for p in "ABC"}
    fleet_comp = {f"N_{p}": sum(x == p for x in prof.values()) for p in "ABC"}
    return {**out, **comp, **fleet_comp}


# ------------------------------------------------------------------ YAML
def seeds_for(index: int) -> list[int]:
    return [SEED_BASE + 10 * index + r for r in range(1, REPS + 1)]


def write_yaml(configs: list[dict], summaries: list[dict]) -> None:
    import yaml
    rows = []
    for i, c in enumerate(configs):
        r = dict(c)
        r["config_index"] = i
        r["seeds"] = seeds_for(i)
        r["session_ids"] = [f"{c['config_id']}_R{rep}" for rep in range(1, REPS + 1)]
        if c["scenario"] in ("S3", "S3-probe", "S3-mixed"):
            r["member_relative_delays_s"] = delays(c["temporal_pattern"], c["K"])
            r["member_attack_duration_s"] = attack_seconds(c["temporal_pattern"])
        if c["scenario"] == "S2":
            r["independent_start_s"] = s2_starts(c["independent_attack_count"], c["independent_timing"])
        if c["scenario"] == "S3-mixed":
            r["independent_start_s"] = [round(WARMUP_S + 0.25 + 0.5 * j, 6) for j in range(c["mixed_independent_count"])]
        rows.append(r)
    doc = {
        "matrix": "m9_expanded_matrix_proposed",
        "status": "PROPOSED - design only; not consumed by any generation runner",
        "approved_for_generation": False,
        "design": "B (balanced, reduced factorial)",
        "baseline_dependency": {"dataset_version": "fleetcan-sim-1.1", "sessions": 610, "fleet_sizes": [2, 5, 10],
                                "status": "immutable; reused unchanged, never regenerated"},
        "proposed_component_dataset_version": "fleetcan-sim-scale-1.0 (proposal; adopt only after approval)",
        "proposed_umbrella_release": "fleetcan-sim-2.0 = fleetcan-sim-1.1 (unchanged, by hash) + fleetcan-sim-scale-1.0 (proposal; NOT adopted)",
        "generator": {"version": "1.3.0", "schema_version": "1.0", "descriptor_version": "1.0",
                      "route_generation": "grid_loops_v1 (generator 1.2.0)", "fleet_configs": "config/m8/fleets/fleet_{20,50,100}.yaml (frozen settings, vehicle_{n:03d})"},
        "frozen_semantics": {"attack_parameters": PARAMS, "attack_duration_s": ATTACK_S, "partial_overlap_attack_duration_s": PARTIAL_ATTACK_S,
                             "warmup_s": WARMUP_S, "tail_s": TAIL_S, "S0_duration_s": 40.0, "S1_duration_s": 20.0},
        "temporal_patterns": {
            "simultaneous": "all members start at campaign_start (frozen)",
            "small": "member i starts at campaign_start + 0.5 s * i (frozen sequential semantics, scripts/m6_build_matrix.py)",
            "medium": "member i at + 2 s * i (frozen)", "large": "member i at + 10 s * i (frozen)",
            "partial": "member i at + 5 s * i with 10 s attacks: adjacent members overlap 5 s (50 %) (frozen)",
            "probe": "replay, K = 2, delays [0, 0.1] s (frozen)",
            "bounded_spread_2s": "NEW: member i at + 2 s * i / (K - 1); total spread 2 s for any K",
            "bounded_spread_10s": "NEW: member i at + 10 s * i / (K - 1); total spread 10 s for any K",
            "S2 near_simultaneous": "independent attack j starts at 10 + 0.5 s * j / (k - 1) (frozen baseline S2)",
            "S2 staggered_0.5s": "NEW: independent attack j starts at 10 + 0.5 s * j (timing identical to coordinated 'small')",
            "S2 separated_10s": "NEW: independent attack j starts at 10 + 10 s * j (timing identical to coordinated 'large'; no overlap)",
            "bounded_spread_0p5s": "NEW: member i at + 0.5 s * i / (K - 1); total spread 0.5 s for any K",
            "mixed": "coordinated 'small' + m independent same-family attacks at 10.25 + 0.5 s * j (frozen baseline mixed)"},
        "member_selection": {
            "rule": "profile-stratified proportional quotas (largest remainder, seeded tie break), seeded choice within each "
                    "profile, seeded permutation = delay rank",
            "streams": {"coordinated": "derive_seed(seed, 'campaign_members/C_MAIN')", "independent (S2, mixed)": "derive_seed(seed, 'independent_targets')",
                        "isolated (S1)": "derive_seed(seed, 'isolated_target')"},
            "inputs": "experiment seed + frozen fleet profile assignment only; no detector information",
            "implementation_note": "resolved by the matrix builder into explicit campaign targets; generator semantics unchanged"},
        "seeds": {"strategy": f"seed = {SEED_BASE} + 10 * config_index + repetition (repetition 1..{REPS})",
                  "derived_streams": "SUMO = seed; profile assignment, switch-on, ECU jitter, route generation, attacks, campaigns: "
                                     "existing derive_seed tags (unchanged); member selection: new tags above"},
        "session_id": "<config_id>_R<repetition>; config_id starts with " + SESSION_PREFIX + " (baseline uses FC11_)",
        "cost_model": COST,
        "candidate_designs": summaries,
        "configurations": rows,
    }
    OUT_YAML.parent.mkdir(parents=True, exist_ok=True)
    OUT_YAML.write_text("# PROPOSED expanded-dataset matrix (Milestone 9). DESIGN ONLY: approved_for_generation is false;\n"
                        "# no runner reads this file. Regenerate with: .venv/bin/python scripts/m9_design.py --write\n"
                        + yaml.safe_dump(doc, sort_keys=False, width=140))


PURPOSE = {
    "S0_scale": "benign control and false-correlation control at each new N",
    "S1_isolated": "one attacked vehicle among N: single incident must not look coordinated",
    "S2_hardneg_near_simultaneous": "hard negative: same family, k vehicles, starts within 0.5 s, coordinated=false",
    "S2_hardneg_staggered": "hard negative matched to coordinated 'small' (same N, k = K, same families)",
    "S2_hardneg_separated": "hard negative matched to coordinated 'large' K = 5 (no temporal overlap)",
    "S3_NK_grid_simultaneous": "N x K core grid: separates fleet-size and campaign-size effects",
    "S3_NK_grid_small_stagger": "N x K grid under 0.5 s sequential stagger (frozen)",
    "S3_temporal_ladder_K5": "frozen timing ladder at fixed K = 5 across N (fleet scaling of temporal evidence)",
    "S3_bounded_spread_largeK": "NEW bounded spread W = 0.5 / 2 / 10 s for K >= 20",
    "S3_probe_replay": "0.1 s replay temporal-resolution probe",
    "S3_mixed": "coordinated campaign + unrelated independent attacks in one fleet",
}


def markdown_tables(configs: list[dict], sums: list[dict]) -> str:
    import itertools
    out = ["## Matrix table (recommended design B, one row per configuration family x N)", "",
           "| config_family | scenario | N | K | attack_type | temporal_pattern | independent_attack_count | mixed_attack_count | duration_s | repetitions | configs | sessions | phase | purpose |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    keyf = lambda c: (list(PURPOSE).index(c["family"]), c["N"])
    for (fi, n), grp in itertools.groupby(sorted(configs, key=keyf), key=keyf):
        g = list(grp)
        fam = g[0]["family"]
        ks = sorted({c["K"] for c in g})
        atk = Counter(c["attack"] for c in g)
        pats = sorted({c["temporal_pattern"] if c["scenario"] != "S2" else c["independent_timing"] for c in g})
        ind = sorted({c["independent_attack_count"] for c in g})
        mix = sorted({c["mixed_independent_count"] for c in g})
        dur = sorted({c["duration_s"] for c in g})
        dtxt = f"{dur[0]:g}" if len(dur) == 1 else f"{dur[0]:g}-{dur[-1]:g}"
        atxt = "none" if fam == "S0_scale" else "all 4" if len(atk) == 4 and len(set(atk.values())) == 1 and len(g) % 4 == 0 and len(g) == 4 * len(ks) * len(pats) \
            else ", ".join(f"{a[:4]}x{v}" for a, v in sorted(atk.items()))
        out.append(f"| {fam} | {g[0]['scenario']} | {n} | {'/'.join(map(str, ks))} | {atxt} | {'/'.join(pats)} | "
                   f"{'/'.join(map(str, ind))} | {'/'.join(map(str, mix))} | {dtxt} | {REPS} | {len(g)} | {REPS * len(g)} | {g[0].get('phase', '')} | {PURPOSE[fam]} |")
    tot = len(configs)
    out += [f"| **total** | | | | | | | | | | **{tot}** | **{REPS * tot}** | | |", ""]
    # N x K coverage (S3 configurations, all patterns)
    out += ["## N x K coverage of coordinated campaigns (configurations; baseline cells marked b)", "",
            "| N \\ K | 2 | 5 | 10 | 20 | 50 | 100 |", "|---|---|---|---|---|---|---|"]
    spec = json.loads((ROOT / "config" / "final" / "matrix" / "matrix_spec.json").read_text())
    bcnt = defaultdict(Counter)
    for r in spec["runs"]:
        if r["repetition"] == 1 and r["scenario_id"] == "S3":
            bcnt[(r["fleet_size"], r["campaign_size"])][r["pattern"]] += 1
    base = {n: {k: f"b{sum(v.values())} ({', '.join(f'{p}:{x}' for p, x in sorted(v.items()))})" for (nn, k), v in bcnt.items() if nn == n}
            for n in (2, 5, 10)}
    for n in (2, 5, 10) + NEW_N:
        cells = []
        for k in K_LADDER:
            if k > n:
                cells.append("-")
            elif n in base:
                cells.append(base[n].get(k, "-"))
            else:
                g = [c for c in configs if c["scenario"].startswith("S3") and c["N"] == n and c["K"] == k]
                pats = Counter(c["temporal_pattern"] for c in g)
                cells.append(f"{len(g)} ({', '.join(f'{p}:{v}' for p, v in sorted(pats.items()))})" if g else "-")
        out.append(f"| {n} | " + " | ".join(cells) + " |")
    out += ["", "## Penetration rho = K / N represented (coordinated campaigns)", "", "| rho | (N, K) cells |", "|---|---|"]
    rho = defaultdict(set)
    for n, k in bcnt:
        rho[round(k / n, 3)].add(f"({n},{k})b")
    for c in configs:
        if c["K"] >= 2:
            rho[round(c["K"] / c["N"], 3)].add(f"({c['N']},{c['K']})")
    for r in sorted(rho):
        out.append(f"| {r:g} | {' '.join(sorted(rho[r], key=lambda x: (len(x), x)))} |")
    out += ["", "## Attack family x configuration family (configurations)", "",
            "| config_family | flooding | fuzzy | malfunction | replay |", "|---|---|---|---|---|"]
    for fam in PURPOSE:
        cnt = Counter(c["attack"] for c in configs if c["family"] == fam)
        if fam != "S0_scale":
            out.append(f"| {fam} | " + " | ".join(str(cnt.get(a, 0)) for a in FAMILIES) + " |")
    tot = Counter(c["attack"] for c in configs if c["attack"] != "none")
    out += ["| **total** | " + " | ".join(f"**{tot[a]}**" for a in FAMILIES) + " |", ""]
    out += ["## Candidate design comparison", "",
            "| design | configs | sessions | simulated h | frames (M) | descriptors (M) | storage GB | gen+analysis h | GB N20/N50/N100 | longest session |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for sm in sums:
        g = sm["storage_gb_per_N"]
        out.append(f"| {sm['design']} | {sm['configurations']} | {sm['sessions']} | {sm['simulated_hours']} | {sm['frames_million']} | "
                   f"{sm['descriptors_million']} | {sm['storage_gb']} | {sm['generation_hours']} | {g.get(20)}/{g.get(50)}/{g.get(100)} | {sm['longest_session']} |")
    return "\n".join(out) + "\n"


def calibrate() -> list[dict]:
    """Compare the cost model with every measured M8.1 (generator 1.3.0) session at N >= 20 (excluding the deliberately
    over-saturated isolation test and repeated chunk-size runs)."""
    import csv
    out = []
    with open(ROOT / "results" / "m8_1_memory_scalability" / "memory_metrics.csv") as fh:
        for m in csv.DictReader(fh):
            n = int(m["fleet_size"])
            if n < 20 or "OVERSAT" in m["session_id"] or "_C10K" in m["session_id"] or "_C100K" in m["session_id"]:
                continue
            k = int(m["campaign_size"] or 0)
            fam = m["attack_type"]
            c = {"N": n, "duration_s": float(m["duration_s"]), "attack": fam, "temporal_pattern": "x", "scenario": m["scenario"],
                 "K": k, "mixed_independent_count": 0, "independent_attack_count": k}
            if c["scenario"] == "S2" and k == 0:
                c["independent_attack_count"] = 3
            pred = session_cost(c)
            meas_wall = float(m["wall_time_s"]) + float(m["analysis_wall_s"] or 0)
            out.append({"session_id": m["session_id"], "N": n, "duration_s": c["duration_s"], "attack": fam, "members": members(c),
                        "frames_measured": int(m["frames_generated"]), "frames_predicted": round(pred["frames"]),
                        "frames_err_pct": round(100 * (pred["frames"] / int(m["frames_generated"]) - 1), 2),
                        "mb_measured": float(m["output_size_mb"]), "mb_predicted": round(pred["mb"], 1),
                        "mb_err_pct": round(100 * (pred["mb"] / float(m["output_size_mb"]) - 1), 1),
                        "time_measured_s": round(meas_wall, 1), "time_predicted_s": round(pred["gen_s"] + pred["analysis_s"], 1),
                        "time_err_pct": round(100 * ((pred["gen_s"] + pred["analysis_s"]) / meas_wall - 1), 1) if float(m["analysis_wall_s"] or 0) else None})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    a = ap.parse_args()
    if a.calibrate:
        rows = calibrate()
        for r in rows:
            print(r)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        import csv
        with open(OUT_DIR / "cost_model_calibration.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        sys.exit(0)
    designs = {"A_minimal": design_A(), "B_balanced": design_B(), "C_comprehensive": design_C()}
    sums = [summarize(k, v) for k, v in designs.items()]
    for s in sums:
        print(json.dumps(s))
    if a.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "design_comparison.json").write_text(json.dumps(sums, indent=1))
        import csv
        for k, v in designs.items():
            with open(OUT_DIR / f"configs_{k}.csv", "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(v[0].keys()) + ["sessions", "est_frames_per_session", "est_mb_per_session"])
                w.writeheader()
                for c in v:
                    sc = session_cost(c)
                    w.writerow({**c, "sessions": c["repetitions"], "est_frames_per_session": round(sc["frames"]), "est_mb_per_session": round(sc["mb"], 1)})
        write_yaml(designs["B_balanced"], sums)
        (OUT_DIR / "tables_B_balanced.md").write_text(markdown_tables(designs["B_balanced"], sums))
        rows = []
        for i, c in enumerate(designs["B_balanced"]):
            for rep, seed in enumerate(seeds_for(i), start=1):
                r = resolve_session(c, seed)
                rows.append({"session_id": f"{c['config_id']}_R{rep}", "seed": seed, "N": c["N"], "K": c["K"], "scenario": c["scenario"],
                             **{k: r[k] for k in ("N_A", "N_B", "N_C", "K_A", "K_B", "K_C")},
                             "coordinated": " ".join(r["coordinated"]), "independent": " ".join(r["independent"]),
                             "isolated": " ".join(r["isolated"])})
        with open(OUT_DIR / "members_B_balanced.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} session member rows")
        print(f"wrote {OUT_YAML}")
