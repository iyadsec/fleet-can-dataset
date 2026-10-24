"""M12: experimental matrix of fleetcan-sim-2.0 (behavioural procedures, campaign controller, hard negatives).

Every session is resolved here, from its seed only, into the exact campaign/attack configuration the generator
runs: members (seeded, profile-balanced over 8 profiles), attack procedure (sampled parameters), per-member
variation (coordination strength), command time, controller pattern offsets, delivery-latency model and trigger.
Output: configs/fleetcan_sim_2_0.yaml (frozen later by SHA-256) and a coverage/cost summary on stdout.

Design: docs/m12_fleetcan_sim_2_0_design.md.  Usage: .venv/bin/python scripts/fcs2_design.py [--write]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import _load_profiles, _read_yaml, build_fleet, derive_seed   # noqa: E402

OUT = ROOT / "configs" / "fleetcan_sim_2_0.yaml"
MATRIX_ID = "fleetcan-sim-2.0-matrix-v1"
DATASET = "fleetcan-sim-2.0"
PREFIX = "FCS2_"
SEED_BASE = 2_000_000
REPS = 5
FAMILIES = ("flooding", "fuzzy", "malfunction", "replay")
NONREPLAY = ("flooding", "fuzzy", "malfunction")
STRENGTHS = ("identical", "mild", "strong")
VAR = {"identical": 0.0, "mild": 0.05, "strong": 0.15}
NS = (20, 50, 100)
K_LADDER = (2, 5, 10, 20, 50, 100)
PROFILES = [f"profile_{p}" for p in "ABCDEFGH"]
LATENCY = {"median_ms": 80.0, "sigma_log": 0.6, "cap_ms": 1000.0}
TAIL_S, COND_TIMEOUT_S = 5.0, 20.0
ROLES = ("vehicle_speed", "engine_rpm", "accelerator", "brake", "steering")
# malfunction value spaces: safe for the DBC range of the role signal in all 8 profiles
MALF = {
    "vehicle_speed": {"constant": (120, 250), "offset": (20, 60), "scale": (1.5, 3.0), "ramp": (0, (150, 250))},
    "engine_rpm": {"constant": (5000, 7500), "offset": (1500, 3000), "scale": (1.5, 1.9), "ramp": (800, (6000, 7500))},
    "accelerator": {"constant": (70, 95), "ramp": (0, (80, 95))},
    "brake": {"constant": (5, 12), "offset": (3, 6), "ramp": (0, (6, 10))},
    "steering": {"constant": (90, 400), "offset": (30, 120), "scale": (2.0, 4.0), "ramp": (0, (200, 500))},
}


# ----------------------------------------------------------------------------- fleet / profiles
def profiles():
    raw = {}
    for pf in ("config/profiles/profiles_abc.yaml", "config/fcs2/profiles_d_h.yaml"):
        raw.update(_read_yaml(ROOT / pf)["profiles"])
    return _load_profiles(raw, ROOT)


PROF = profiles()


def fleet_section(n: int) -> dict:
    return yaml.safe_load((ROOT / "config" / "fcs2" / "fleets" / f"fleet_{n}.yaml").read_text())["fleet"]


def fleet_for(n: int, seed: int):
    return build_fleet(fleet_section(n), {p: None for p in PROFILES}, seed)[0]


def stratified(fleet, k: int, rng, exclude=()) -> list[str]:
    """Seeded, profile-balanced selection of k vehicles in seeded order (same rule as scripts/m9_design.py)."""
    excl = set(exclude)
    pool = [a for a in fleet if a.vehicle_id not in excl]
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


def role_msg(profile: str, role: str):
    m, _ = PROF[profile].roles[role]
    spec = next(x for x in PROF[profile].messages if x.name == m)
    return m, spec.period_ms


def frame_id(profile: str, msg: str) -> int:
    from src.can_generator import load_dbc
    return load_dbc(PROF[profile].dbc).get_message_by_name(msg).frame_id


# ----------------------------------------------------------------------------- procedures
def r1(x, q=1):
    return float(round(x / q) * q)


def sample_procedure(family: str, rng) -> dict:
    """One attack procedure: profile-independent parameters (roles, not IDs, for profile-specific targets)."""
    if family == "flooding":
        dlc = int(rng.choice([2, 4, 8]))
        pay = "random" if rng.random() < 0.3 else bytes(rng.integers(0, 256, dlc, dtype=np.uint8).tolist()).hex().upper()
        p = {"can_id": int(rng.integers(0x000, 0x080)), "rate_hz": float(rng.choice([400, 800, 1600, 3000, 4500])),
             "dlc": dlc, "payload": pay}
    elif family == "fuzzy":
        mode = str(rng.choice(["id", "payload", "both"]))
        p = {"mode": mode, "rate_hz": r1(rng.uniform(200, 1500), 50), "dlc": "random" if rng.random() < 0.5 else 8}
        if mode == "payload":
            p["target"] = str(rng.choice(ROLES))
            p["dlc"] = 8
        else:
            width = int(rng.choice([64, 256, 2048]))
            lo = int(rng.integers(0, 0x800 - width + 1)) if width < 2048 else 0
            p["id_range"] = [lo, lo + width - 1]
        if mode == "id":
            p["dlc"] = 8
            p["payload"] = "zeros" if rng.random() < 0.5 else bytes(rng.integers(0, 256, 8, dtype=np.uint8).tolist()).hex().upper()
    elif family == "malfunction":
        role = str(rng.choice(ROLES))
        space = MALF[role]
        strat = str(rng.choice(sorted(space)))
        mode = "replace" if rng.random() < 0.4 else "inject"
        if strat == "ramp":
            lo, (a, b) = space["ramp"]
            man = {"strategy": "ramp", "from": float(lo), "to": r1(rng.uniform(a, b), 1)}
        else:
            a, b = space[strat]
            v = r1(rng.uniform(a, b), 0.01 if strat == "scale" else 1)
            if role == "steering" and strat in ("constant", "offset") and rng.random() < 0.5:
                v = -v
            man = {"strategy": strat, {"constant": "value", "offset": "offset", "scale": "factor"}[strat]: v}
        p = {"mode": mode, "target": role, "manipulation": man}
        if mode == "inject":
            p["rate_multiplier"] = int(rng.choice([1, 2, 5, 10]))
    else:  # replay
        L = r1(rng.uniform(1.0, 8.0), 0.5)
        p = {"window_length_s": L, "time_offset_ms": r1(rng.uniform(0.1, 5.0), 0.05), "source_vehicle": "self"}
        if rng.random() < 0.4:
            k = int(rng.integers(2, 5))
            p["roles_subset"] = sorted(str(x) for x in rng.choice(ROLES, size=k, replace=False))
    dur = r1(rng.uniform(2.0, 10.0), 0.5) if family != "replay" else p["window_length_s"]
    proc = {"family": family, "params": p, "duration_s": dur}
    proc["procedure_id"] = "P" + hashlib.sha256(json.dumps(proc, sort_keys=True).encode()).hexdigest()[:10]
    return proc


def member_params(proc: dict, vehicle: str, profile: str, strength: str, rng) -> tuple[dict, float]:
    """Resolve a procedure for one vehicle (its profile's IDs / periods) with the strength's variation."""
    v = VAR[strength]
    f = (lambda x: x) if v == 0 else (lambda x: x * float(rng.uniform(1 - v, 1 + v)))
    fam, p = proc["family"], json.loads(json.dumps(proc["params"]))
    dur = proc["duration_s"]
    if fam in ("flooding", "fuzzy"):
        p["rate_hz"] = round(f(p["rate_hz"]), 3)
        dur = round(f(dur), 3)
    elif fam == "malfunction":
        man = p["manipulation"]
        for key in ("value", "offset", "factor", "to"):
            if key in man:
                man[key] = round(f(man[key]), 3)
        if p["mode"] == "inject":
            _, period = role_msg(profile, p["target"])
            p["rate_hz"] = round(f(p.pop("rate_multiplier") * 1000.0 / period), 3)
        dur = round(f(dur), 3)
    else:
        L = round(f(p.pop("window_length_s")), 3)
        p["window_relative_s"] = [-L, 0.0]
        p["time_offset_ms"] = round(f(p["time_offset_ms"]), 4)
        roles = p.pop("roles_subset", None)
        if roles:
            p["can_ids"] = sorted({frame_id(profile, role_msg(profile, r_)[0]) for r_ in roles})
        dur = L
    if strength == "identical":
        p["rng_scope"] = "campaign"
    p["procedure_id"] = proc["procedure_id"]
    return p, dur


# ----------------------------------------------------------------------------- timing
def offsets(pattern: str, k: int, rng, info: dict) -> list[float]:
    if pattern == "broadcast" or pattern == "condition":
        return [0.0] * k
    if pattern == "waves":
        g = min(k, int(rng.choice([2, 3, 4])))
        w = float(rng.choice([1.0, 3.0, 5.0]))
        info.update({"waves": g, "wave_spacing_s": w})
        return [w * (i % g) for i in range(k)]
    if pattern == "sequential":
        d = float(rng.choice([0.5, 2.0]))
        info["step_s"] = d
        return [round(d * i, 6) for i in range(k)]
    if pattern == "probe":
        info["step_s"] = 0.1
        return [round(0.1 * i, 6) for i in range(k)]
    if pattern == "partial":
        info["step_s"] = 5.0
        return [5.0 * i for i in range(k)]
    if pattern == "bounded":
        w = float(rng.choice([0.5, 2.0, 10.0]))
        info["spread_s"] = w
        return [round(w * i / (k - 1), 9) for i in range(k)]
    raise ValueError(pattern)


def trigger(rng) -> dict:
    if rng.random() < 0.6:
        return {"quantity": "speed_kmh", "op": ">=", "value": float(rng.choice([30.0, 35.0, 40.0])), "timeout_s": COND_TIMEOUT_S}
    return {"quantity": "accel_mps2", "op": "<=", "value": -1.0, "timeout_s": COND_TIMEOUT_S}


# ----------------------------------------------------------------------------- matrix cells
def cell(scenario, n, family, k=0, pattern="none", strength="none", hard_negative="none", n_ind=0, note=""):
    tag = {"S3": f"K{k:03d}_{pattern}_{strength}", "S3-mixed": f"K{k:03d}_broadcast_{strength}_m{n_ind:02d}",
           "S3-probe": f"K{k:03d}_probe_{strength}", "S1": "K000_isolated"}.get(scenario, f"k{n_ind:02d}")
    cid = f"{PREFIX}N{n:03d}_{scenario}_{family}_{tag}" + ("_ladder" if note == "strength_ladder" else "")
    return {"config_id": cid, "scenario": scenario, "N": n, "family": family, "K": k, "pattern": pattern,
            "strength": strength, "hard_negative": hard_negative, "independent_count": n_ind, "note": note}


def design() -> list[dict]:
    c = [cell("S0", n, "none") for n in NS]
    c += [cell("S1", n, f) for n in NS for f in FAMILIES]
    cell_i = 0
    for n in NS:                                                       # S3 core: N x K x pattern
        for k in (x for x in K_LADDER if x <= n):
            pats = ["broadcast", "waves", "sequential" if k <= 20 else "bounded", "condition"]
            for j, pat in enumerate(pats):                             # Latin rotation: family and strength vary
                fam = NONREPLAY[cell_i % 3] if pat == "condition" else FAMILIES[(cell_i + j) % 4]   # with pattern
                c.append(cell("S3", n, fam, k, pat, STRENGTHS[(cell_i + j) % 3]))
            cell_i += 1
    for j, n in enumerate(NS):                                         # partial overlap at K = 5
        c.append(cell("S3", n, FAMILIES[j % 4], 5, "partial", STRENGTHS[j % 3]))
    for f in FAMILIES:                                                 # explicit strength ladder at N = 50, K = 10
        for s in STRENGTHS:
            c.append(cell("S3", 50, f, 10, "broadcast", s, note="strength_ladder"))
    j = 0
    for n in NS:                                                       # hard negatives (independent attacks)
        for k in (5, 10):
            for hn in ("diverse", "behaviour", "timing", "both"):
                if hn == "diverse":
                    for f in (FAMILIES[j % 4], FAMILIES[(j + 2) % 4]):
                        c.append(cell("S2-" + hn, n, f, n_ind=k, hard_negative="none"))
                else:
                    c.append(cell("S2-" + hn, n, FAMILIES[j % 4], n_ind=k, hard_negative=hn))
                j += 1
    for j, n in enumerate(NS):                                         # mixed: campaign + unrelated attacks
        for f in (FAMILIES[j % 4], FAMILIES[(j + 1) % 4]):
            c.append(cell("S3-mixed", n, f, 5 if n == 20 else 10, "broadcast", STRENGTHS[j % 3], n_ind=2 if n == 20 else 5))
    for j, n in enumerate(NS):                                         # 0.1 s probe
        c.append(cell("S3-probe", n, ("replay", "flooding", "malfunction")[j], 2, "probe", "identical"))
    return c


# ----------------------------------------------------------------------------- session resolution
def resolve(cfg: dict, seed: int) -> dict:
    fleet = fleet_for(cfg["N"], seed)
    prof = {a.vehicle_id: a.profile for a in fleet}
    rng = lambda tag: np.random.default_rng(derive_seed(seed, f"fcs2/{tag}"))    # noqa: E731
    T = r1(rng("command").uniform(8.0, 20.0), 0.1)
    out = {"command_s": T, "campaigns": [], "attacks": []}
    end = T

    def campaign(cid, coordinated, members, proc, strength, pattern, start, offs, info, hn, trig=None):
        nonlocal end
        r = rng(f"strength/{cid}")
        vp, durs = {}, []
        for v in members:
            p, d = member_params(proc, v, prof[v], strength if coordinated else "identical", r)
            if not coordinated:
                p.pop("rng_scope", None)
            vp[v] = p
            durs.append(d)
        cc = {"campaign_id": cid, "coordinated": coordinated, "attack_type": proc["family"], "start_s": start,
              "delay_pattern": "controller", "controller_pattern": pattern, "procedure_id": proc["procedure_id"],
              "strength": strength if coordinated else "n/a", "hard_negative": hn, "offsets_s": offs,
              "latency": dict(LATENCY), "targets": members, "durations_s": durs, "params": {},
              "vehicle_params": vp, "pattern_info": info}
        if proc["family"] == "replay":
            cc["params"] = {}
        if trig:
            cc["trigger"] = trig
        out["campaigns"].append(cc)
        extra = COND_TIMEOUT_S + 0.01 if trig else 0.0
        end = max(end, start + max(o + d for o, d in zip(offs, durs)) + LATENCY["cap_ms"] / 1000 + extra)

    sc, fam = cfg["scenario"], cfg["family"]
    if sc == "S1":
        v = stratified(fleet, 1, rng("isolated"))[0]
        proc = sample_procedure(fam, rng("procedure/iso"))
        p, d = member_params(proc, v, prof[v], "identical", rng("strength/iso"))
        p.pop("rng_scope", None)
        if fam == "replay":                    # isolated attacks need the absolute window (no campaign scheduler)
            w = p.pop("window_relative_s")
            p["window_s"] = [round(T + w[0], 9), round(T + w[1], 9)]
        out["attacks"].append({"attack_id": f"ISO_{fam.upper()}_{v}", "type": fam, "vehicle": v, "start_s": T,
                               "duration_s": d, "params": p})
        end = T + d
    elif sc in ("S3", "S3-mixed", "S3-probe"):
        members = stratified(fleet, cfg["K"], rng("members"))
        proc = sample_procedure(fam, rng("procedure/main"))
        info = {}
        pat = cfg["pattern"]
        offs = offsets(pat, cfg["K"], rng("pattern"), info)
        durs_override = None
        if pat == "partial":
            proc = dict(proc, duration_s=10.0)
            if fam == "replay":
                proc["params"] = dict(proc["params"], window_length_s=10.0)
        campaign(f"C_{fam.upper()}_MAIN", True, members, proc, cfg["strength"], pat, T, offs, info, "none",
                 trigger(rng("trigger")) if pat == "condition" else None)
        if sc == "S3-mixed":
            ind = stratified(fleet, cfg["independent_count"], rng("independent"), exclude=members)
            starts = rng("ind_times").uniform(0.0, 15.0, size=len(ind))
            for j, v in enumerate(ind):
                pr = sample_procedure(fam, rng(f"procedure/ind{j}"))
                campaign(f"C_{fam.upper()}_IND{j + 1:02d}", False, [v], pr, "n/a", "independent", round(T + float(starts[j]), 3),
                         [0.0], {}, "none")
    elif sc.startswith("S2-"):
        k = cfg["independent_count"]
        ind = stratified(fleet, k, rng("independent"))
        hn = cfg["hard_negative"]
        shared = sample_procedure(fam, rng("procedure/shared"))
        if hn in ("timing", "both"):                                   # coordinated-looking start times
            pat = "broadcast" if rng("hn_pattern").random() < 0.5 else "sequential"
            info = {}
            offs = offsets(pat, k, rng("pattern"), info)
        else:                                                          # unrelated arrivals over a random span
            span = float(rng("span").choice([5.0, 15.0, 30.0]))
            offs = [round(float(x), 3) for x in rng("ind_times").uniform(0.0, span, size=k)]
            pat, info = "independent", {"span_s": span}
        lat = rng("ind_latency")
        for j, v in enumerate(ind):
            pr = shared if hn in ("behaviour", "both") else sample_procedure(fam, rng(f"procedure/ind{j}"))
            # same delivery-latency model as the controller, drawn per attack, so timing statistics match
            l_ = min(LATENCY["cap_ms"], LATENCY["median_ms"] * float(np.exp(lat.normal(0.0, LATENCY["sigma_log"])))) / 1000
            campaign(f"C_{fam.upper()}_IND{j + 1:02d}", False, [v], pr, "n/a", pat, round(T + offs[j] + l_, 6), [0.0],
                     dict(info, slot=j), hn)
    out["duration_s"] = round(end + TAIL_S, 3) if sc != "S0" else 40.0
    out["members"] = {c_["campaign_id"]: c_["targets"] for c_ in out["campaigns"]}
    comp = Counter(prof.values())
    out["fleet_composition"] = {p[-1]: comp[p] for p in PROFILES}
    return out


# ----------------------------------------------------------------------------- output
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    cfgs = design()
    ids = [c["config_id"] for c in cfgs]
    assert len(ids) == len(set(ids)), [x for x, n in Counter(ids).items() if n > 1]
    sessions = []
    for idx, c in enumerate(cfgs):
        for rep in range(1, REPS + 1):
            seed = SEED_BASE + 10 * idx + rep
            sid = f"{c['config_id']}_R{rep}"
            sessions.append({"session_id": sid, "config_id": c["config_id"], "config_index": idx, "repetition": rep,
                             "seed": seed, **{k: c[k] for k in ("scenario", "N", "family", "K", "pattern", "strength",
                                                                "hard_negative", "independent_count", "note")},
                             **resolve(c, seed)})
    # coverage and cost
    n_frames = sum(s["N"] * 330 * s["duration_s"] for s in sessions)
    att = Counter((s["scenario"], s["family"]) for s in sessions)
    print(f"configurations {len(cfgs)}, sessions {len(sessions)}, simulated vehicle-hours {sum(s['N'] * s['duration_s'] for s in sessions) / 3600:.1f}")
    print(f"benign frames ~{n_frames / 1e6:.0f} M; mean duration {np.mean([s['duration_s'] for s in sessions]):.1f} s, max {max(s['duration_s'] for s in sessions):.1f} s")
    print("by scenario:", Counter(s["scenario"] for s in sessions))
    print("S3 pattern x family:", Counter((s["pattern"], s["family"]) for s in sessions if s["scenario"] == "S3"))
    print("S3 strength:", Counter(s["strength"] for s in sessions if s["scenario"] == "S3"))
    print("S3 K:", Counter(s["K"] for s in sessions if s["scenario"] == "S3"))
    if a.write:
        doc = {"matrix_id": MATRIX_ID, "dataset_version": DATASET, "status": "frozen 2026-10-24 (after pilot)",
               "design": "docs/m12_fleetcan_sim_2_0_design.md", "seed_base": SEED_BASE, "repetitions": REPS,
               "latency": LATENCY, "configurations": cfgs, "sessions": sessions}
        OUT.write_text("# GENERATED by scripts/fcs2_design.py\n" + yaml.safe_dump(doc, sort_keys=False, width=160))
        print("written", OUT)


if __name__ == "__main__":
    main()
