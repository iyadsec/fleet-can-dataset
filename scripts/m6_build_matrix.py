"""Milestone 6: build the frozen fleetcan-sim-1.1 experiment matrix (122 configurations x 5 seeds = 610 runs)
from docs/final_protocol.md. Writes, BEFORE any execution:

  config/final/matrix/matrix_spec.json          one record per run (provenance)
  config/final/matrix/<config_id>/campaigns.yaml | attacks.yaml
  config/final/matrix/<config_id>/experiment_r<k>.yaml   (k = 1..5)

Frozen rules (docs/final_protocol.md, generator 1.1.0):
  fleets 2/5/10 -> config/final/fleets/fleet_N.yaml (A/B/C balanced_shuffle, switch-on [0,1] s, jitter +-1 %)
  attacks: flooding 0x000 1600/s 5 s; fuzzy random ID+payload 1000/s 5 s; malfunction inject vehicle_speed=250
           km/h 100/s 5 s; replay own bus, 5 s window, 5 s attack (+0.25 ms offset); partial overlap: 10 s attacks
  timing: attacks start at 10 s (warm-up), runs end 5 s after the last attack
  S0: 1 benign; S1: 1 isolated attack on vehicle_01 per family
  S2: per family k = 2/3/5 (N = 2/5/10) independent single-vehicle campaigns on vehicle_01..k, starts evenly
      spaced over [10.0, 10.5] s
  S3: per family: size N x {simultaneous 0, small 0.5 s, medium 2 s, large 10 s, partial overlap 5 s / 10 s};
      sizes 2 and 5 (< N) x {simultaneous, small}; members vehicle_01..size, delays = step x member index
  S3-mixed (N >= 5): per family coordinated small stagger of size 2 (N=5) / 5 (N=10) on vehicle_01..k plus
      1 (N=5) / 2 (N=10) independent same-family attacks on the next vehicles starting at 10.25 + 0.5 j s
  S3-probe: replay, size 2, 0.1 s
  seeds: seed = 610000 + 10 x config_index + repetition (repetition 1..5)

Usage: python3 scripts/m6_build_matrix.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "final" / "matrix"
DATASET_VERSION = "fleetcan-sim-1.1"
REPS = 5
FAMILIES = ["flooding", "fuzzy", "malfunction", "replay"]
STEP = {"simultaneous": 0.0, "small": 0.5, "medium": 2.0, "large": 10.0, "partial": 5.0, "probe": 0.1}
PARAMS = {
    "flooding": "{can_id: 0x000, rate_hz: 1600, dlc: 8, payload: zeros}",
    "fuzzy": "{mode: both, rate_hz: 1000, id_range: [0x000, 0x7FF], dlc: random}",
    "malfunction": "{mode: inject, target: vehicle_speed, manipulation: {strategy: constant, value: 250.0}, rate_hz: 100}",
}


def replay_params(window_s: float) -> str:
    return f"{{window_relative_s: [-{window_s}, 0.0], source_vehicle: self, time_offset_ms: 0.25}}"


def campaign_yaml(cid, family, targets, start, pattern, delays, attack_s, coordinated=True):
    lines = [f"  - campaign_id: {cid}", f"    attack_type: {family}", f"    coordinated: {str(coordinated).lower()}",
             f"    targets: [{', '.join(targets)}]", f"    start_s: {start}"]
    if pattern == "simultaneous":
        lines.append("    delay_pattern: simultaneous")
    else:
        lines += ["    delay_pattern: fixed", f"    relative_delays_s: [{', '.join(f'{d:g}' for d in delays)}]"]
    if family == "replay":
        lines.append(f"    params: {replay_params(attack_s)}")
    else:
        lines += [f"    duration_s: {attack_s:g}", f"    params: {PARAMS[family]}"]
    return "\n".join(lines) + "\n"


def vehicles(n):
    return [f"vehicle_{i:02d}" for i in range(1, n + 1)]


def configurations():
    cfgs = []
    for n in (2, 5, 10):
        V = vehicles(n)

        def add(scenario, family, size, pattern, body, kind, end_s, note=""):
            cid = f"N{n:02d}_{scenario}_{family}_{'s%02d' % size if size else 'none'}_{pattern}"
            cfgs.append({"config_id": cid, "fleet_size": n, "scenario": scenario, "scenario_id": scenario.split("-")[0],
                         "attack": family, "campaign_size": size, "pattern": pattern, "kind": kind, "body": body,
                         "duration_s": round(end_s, 3), "note": note})

        add("S0", "none", 0, "none", None, None, 40.0)
        for f in FAMILIES:
            # S1 isolated (attacks file, no campaign)
            if f == "replay":
                p = "{source_vehicle: vehicle_01, window_s: [5.0, 10.0], can_ids: all, time_offset_ms: 0.25}"
                atk = f"  - attack_id: A_S1_REPLAY\n    type: replay\n    vehicle: vehicle_01\n    start_s: 10.0\n    params: {p}\n"
            else:
                atk = (f"  - attack_id: A_S1_{f.upper()}\n    type: {f}\n    vehicle: vehicle_01\n    start_s: 10.0\n"
                       f"    duration_s: 5.0\n    params: {PARAMS[f]}\n")
            add("S1", f, 1, "isolated", "attacks:\n" + atk, "attacks", 20.0)
            # S2 independent hard negatives
            k = {2: 2, 5: 3, 10: 5}[n]
            starts = [round(10.0 + 0.5 * j / (k - 1), 4) for j in range(k)]
            body = "".join(campaign_yaml(f"C_S2_{f.upper()}_{j + 1}", f, [V[j]], s, "simultaneous", [0.0], 5.0, coordinated=False)
                           for j, s in enumerate(starts))
            add("S2", f, k, "independent", "campaigns:\n" + body, "campaigns", starts[-1] + 5.0 + 5.0,
                note=f"{k} independent single-vehicle campaigns, starts {starts}")
            # S3 coordinated
            for size in [s for s in (2, 5, 10) if s <= n]:
                pats = ["simultaneous", "small", "medium", "large", "partial"] if size == n else ["simultaneous", "small"]
                for pat in pats:
                    attack_s = 10.0 if pat == "partial" else 5.0
                    delays = [STEP[pat] * i for i in range(size)]
                    body = campaign_yaml(f"C_S3_{f.upper()}", f, V[:size], 10.0, pat, delays, attack_s)
                    add("S3", f, size, pat, "campaigns:\n" + body, "campaigns", 10.0 + delays[-1] + attack_s + 5.0)
            # S3-mixed
            if n >= 5:
                k, m = {5: (2, 1), 10: (5, 2)}[n]
                delays = [0.5 * i for i in range(k)]
                body = campaign_yaml(f"C_MIX_{f.upper()}", f, V[:k], 10.0, "small", delays, 5.0)
                for j in range(m):
                    body += campaign_yaml(f"C_MIX_{f.upper()}_IND{j + 1}", f, [V[k + j]], 10.25 + 0.5 * j,
                                          "simultaneous", [0.0], 5.0, coordinated=False)
                add("S3-mixed", f, k, "small", "campaigns:\n" + body, "campaigns", 10.0 + max(delays[-1], 0.25 + 0.5 * (m - 1)) + 10.0,
                    note=f"coordinated size {k} + {m} independent")
        body = campaign_yaml("C_PROBE_REPLAY", "replay", V[:2], 10.0, "probe", [0.0, 0.1], 5.0)
        add("S3-probe", "replay", 2, "probe", "campaigns:\n" + body, "campaigns", 10.0 + 0.1 + 5.0 + 5.0)
    return cfgs


EXPERIMENT = """# fleetcan-sim-1.1 frozen matrix run (generated by scripts/m6_build_matrix.py; do not edit by hand).
experiment:
  experiment_id: {eid}
  dataset_version: {dv}
  scenario_id: {scen}
  description: "fleetcan-sim-1.1 {cid} repetition {rep}"
  seed: {seed}
  tags: {{config_id: {cid}, repetition: {rep}, fleet_size: {n}, scenario: {scenario}, attack: {attack}, campaign_size: {size}, pattern: {pattern}}}
simulation:
  sumo_config: sumo/scenarios/fleet_{n}.sumocfg
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
fleet_config: config/final/fleets/fleet_{n}.yaml
attacks_config: {attacks}
campaigns_config: {campaigns}
"""


def main():
    cfgs = configurations()
    assert len(cfgs) == 122, len(cfgs)
    assert len({c["config_id"] for c in cfgs}) == 122
    OUT.mkdir(parents=True, exist_ok=True)
    runs = []
    for idx, c in enumerate(cfgs):
        d = OUT / c["config_id"]
        d.mkdir(exist_ok=True)
        attacks, campaigns = "config/attacks.yaml", "config/campaigns.yaml"
        if c["kind"] == "attacks":
            (d / "attacks.yaml").write_text(f"# {c['config_id']}: isolated attack (frozen protocol).\n" + c["body"])
            attacks = str((d / "attacks.yaml").relative_to(ROOT))
        elif c["kind"] == "campaigns":
            (d / "campaigns.yaml").write_text(f"# {c['config_id']}: campaigns (frozen protocol). {c['note']}\n" + c["body"])
            campaigns = str((d / "campaigns.yaml").relative_to(ROOT))
        for rep in range(1, REPS + 1):
            seed = 610000 + 10 * idx + rep
            eid = f"FC11_{c['config_id']}_R{rep}"
            path = d / f"experiment_r{rep}.yaml"
            path.write_text(EXPERIMENT.format(eid=eid, dv=DATASET_VERSION, scen=c["scenario_id"], cid=c["config_id"], rep=rep,
                                              seed=seed, n=c["fleet_size"], scenario=c["scenario"], attack=c["attack"],
                                              size=c["campaign_size"], pattern=c["pattern"], dur=c["duration_s"],
                                              attacks=attacks, campaigns=campaigns))
            runs.append({"run_index": len(runs), "config_index": idx, "config_id": c["config_id"], "repetition": rep,
                         "session_id": eid, "seed": seed, "fleet_size": c["fleet_size"], "scenario": c["scenario"],
                         "scenario_id": c["scenario_id"], "attack": c["attack"], "campaign_size": c["campaign_size"],
                         "pattern": c["pattern"], "duration_s": c["duration_s"], "note": c["note"],
                         "experiment_config": str(path.relative_to(ROOT))})
    spec = {"dataset_version": DATASET_VERSION, "generator_version": "1.1.0", "schema_version": "1.0",
            "descriptor_version": "1.0", "protocol": "docs/final_protocol.md", "configurations": len(cfgs),
            "repetitions": REPS, "runs": runs}
    (OUT / "matrix_spec.json").write_text(json.dumps(spec, indent=1))
    print(f"{len(cfgs)} configurations, {len(runs)} runs -> {OUT / 'matrix_spec.json'}")


if __name__ == "__main__":
    main()
