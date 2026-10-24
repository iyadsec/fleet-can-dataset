"""Campaign scheduler: turns campaign configuration into per-vehicle attacks with exact timing.

A campaign is a first-class experiment object. Membership comes ONLY from configuration
(config/.../campaigns_*.yaml); it is never inferred from timing. Each member vehicle gets one
attack (attack_id = <campaign_id>_<vehicle_id>) whose window is fixed on the SUMO timeline:

    vehicle_attack_start = campaign_start + relative_delay
    vehicle_attack_end   = vehicle_attack_start + duration        (replay: + window length + offset)

All times are integer nanoseconds on the logical (SUMO / generation) timeline. CAN queueing,
transmission and capture times never feed back into campaign timing.

Configuration schema (one list entry per campaign):
  campaign_id:     C001                       unique
  coordinated:     true | false               false = independent (exactly one target vehicle)
  attack_type:     flooding | fuzzy | malfunction | replay
  start_s:         10.0                       campaign_start
  delay_pattern:   simultaneous | fixed | uniform_random
  targets:         [vehicle_01, vehicle_02]   explicit membership
  relative_delays_s: [0.0, 1.0]               fixed: one per target, >= 0
  delay_range_s:   [0.0, 5.0]                 uniform_random: seeded, one draw per target
  delay_resolution_s: 0.001                   uniform_random: draws rounded to this resolution
  duration_s:      5.0  | durations_s: [..]   per-vehicle attack duration (not used by replay)
  params:          {...}                      attack parameters shared by all members
  vehicle_params:  {vehicle_02: {...}}        optional per-vehicle overrides
  replay only:     params.window_relative_s: [-5.0, 0.0]   window relative to each vehicle's start
                   params.source_vehicle: self | <vehicle_id>
"""

from __future__ import annotations

import copy
import json
import zlib
from dataclasses import dataclass, field

import numpy as np

NS = 1_000_000_000
PATTERNS = ("simultaneous", "fixed", "uniform_random", "controller")


class CampaignConfigError(ValueError):
    pass


def campaign_seed(experiment_seed: int, campaign_id: str) -> int:
    return int(np.random.SeedSequence([experiment_seed, zlib.crc32(("campaign:" + campaign_id).encode())]).generate_state(1)[0])


def _ns(seconds) -> int:
    return round(float(seconds) * NS)


@dataclass
class Member:
    campaign_id: str
    vehicle_id: str
    attack_id: str
    start_ns: int
    end_ns: int
    relative_delay_ns: int
    params: dict = field(default_factory=dict)
    pattern_offset_ns: int = 0          # generator 2.0.0 (controller pattern): offset of the member's slot
    latency_ns: int = 0                 # generator 2.0.0 (controller pattern): command delivery latency


@dataclass
class Campaign:
    campaign_id: str
    coordinated: bool
    attack_type: str
    delay_pattern: str
    start_ns: int
    seed: int
    params: dict
    raw: dict
    members: list[Member] = field(default_factory=list)

    @property
    def end_ns(self) -> int:
        return max(m.end_ns for m in self.members)

    @property
    def first_start_ns(self) -> int:
        return min(m.start_ns for m in self.members)

    def inter_vehicle_delays_ns(self) -> list[int]:
        s = sorted(m.start_ns for m in self.members)
        return [b - a for i, a in enumerate(s) for b in s[i + 1:]]

    def common_overlap_ns(self) -> int:
        """Length of the interval in which EVERY member vehicle is under attack (0 if none)."""
        return max(0, min(m.end_ns for m in self.members) - max(m.start_ns for m in self.members))


def pair_overlaps(c: Campaign) -> list[dict]:
    """Pairwise interval metrics for members a, b (ordered by start):
       start_delay = start_b - start_a
       overlap     = max(0, min(end_a, end_b) - max(start_a, start_b))
       overlap_ratio_min = overlap / min(duration_a, duration_b)
       jaccard     = overlap / (duration_a + duration_b - overlap)   (intersection over union)"""
    ms = sorted(c.members, key=lambda m: (m.start_ns, m.vehicle_id))
    out = []
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            ov = max(0, min(a.end_ns, b.end_ns) - max(a.start_ns, b.start_ns))
            da, db = a.end_ns - a.start_ns, b.end_ns - b.start_ns
            out.append({"vehicle_a": a.vehicle_id, "vehicle_b": b.vehicle_id,
                        "start_delay_ns": b.start_ns - a.start_ns, "overlap_ns": ov,
                        "overlap_ratio_min": ov / min(da, db), "jaccard": ov / (da + db - ov)})
    return out


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def build_schedule(campaign_configs: list[dict], seed: int, fleet_vehicles: list[str] | None = None) -> list[Campaign]:
    """Validate campaign configuration and compute every member's exact logical timing."""
    campaigns, ids = [], set()
    known = set(fleet_vehicles) if fleet_vehicles is not None else None
    for c in campaign_configs or []:
        cid = c.get("campaign_id")
        if not cid or cid in ids or cid in ("None", ""):
            raise CampaignConfigError(f"missing or duplicate campaign_id {cid!r}")
        ids.add(cid)
        typ = c.get("attack_type")
        if typ not in ("flooding", "fuzzy", "malfunction", "replay"):
            raise CampaignConfigError(f"{cid}: unknown attack_type {typ}")
        coordinated = c.get("coordinated")
        if not isinstance(coordinated, bool):
            raise CampaignConfigError(f"{cid}: coordinated must be true or false")
        targets = list(c.get("targets") or [])
        if not targets or len(set(targets)) != len(targets):
            raise CampaignConfigError(f"{cid}: targets must be a non-empty list without duplicates")
        if known is not None and set(targets) - known:
            raise CampaignConfigError(f"{cid}: unknown target vehicles {sorted(set(targets) - known)}")
        if coordinated and len(targets) < 2:
            raise CampaignConfigError(f"{cid}: a coordinated campaign needs >= 2 target vehicles")
        if not coordinated and len(targets) != 1:
            raise CampaignConfigError(f"{cid}: an independent (non-coordinated) campaign has exactly one target")
        pattern = c.get("delay_pattern", "simultaneous")
        if pattern not in PATTERNS:
            raise CampaignConfigError(f"{cid}: delay_pattern must be one of {PATTERNS}")
        cseed = campaign_seed(seed, cid)
        rng = np.random.default_rng(cseed)
        if pattern == "simultaneous":
            delays = [0] * len(targets)
        elif pattern == "fixed":
            d = c.get("relative_delays_s")
            if not d or len(d) != len(targets):
                raise CampaignConfigError(f"{cid}: fixed pattern needs one relative_delays_s entry per target")
            delays = [_ns(x) for x in d]
        elif pattern == "controller":
            # generator 2.0.0: a command issued at start_s reaches member i after offsets_s[i] (its slot in the
            # pattern: broadcast / waves / sequential / bounded / partial) plus a seeded log-normal delivery latency
            offs = c.get("offsets_s")
            if not offs or len(offs) != len(targets):
                raise CampaignConfigError(f"{cid}: controller pattern needs one offsets_s entry per target")
            lat = dict(c.get("latency") or {})
            med, sig, cap = float(lat.get("median_ms", 0.0)), float(lat.get("sigma_log", 0.0)), float(lat.get("cap_ms", 1000.0))
            lats = [min(cap, med * float(np.exp(rng.normal(0.0, sig)))) if med > 0 else 0.0 for _ in targets]
            offsets_ns = [_ns(x) for x in offs]
            latency_ns = [round(x * 1e6) for x in lats]
            delays = [o + l for o, l in zip(offsets_ns, latency_ns)]
        else:
            lo, hi = c.get("delay_range_s", [0, 0])
            res = _ns(c.get("delay_resolution_s", 0.001))
            delays = [round(_ns(rng.uniform(float(lo), float(hi))) / res) * res for _ in targets]
        if any(x < 0 for x in delays):
            raise CampaignConfigError(f"{cid}: relative delays must be >= 0")

        start = _ns(c["start_s"])
        durations = c.get("durations_s") or [c.get("duration_s")] * len(targets)
        if len(durations) != len(targets):
            raise CampaignConfigError(f"{cid}: durations_s needs one entry per target")
        camp = Campaign(cid, coordinated, typ, pattern, start, cseed, dict(c.get("params") or {}), c)
        for i, (vid, rel, dur) in enumerate(zip(targets, delays, durations)):
            params = _deep_merge(camp.params, (c.get("vehicle_params") or {}).get(vid, {}))
            if c.get("trigger"):                      # generator 2.0.0: start = when the member's own state meets it
                if typ == "replay":
                    raise CampaignConfigError(f"{cid}: condition triggers are not supported for replay")
                params["trigger"] = dict(c["trigger"])
            vstart = start + rel
            if typ == "replay":
                w = params.pop("window_relative_s", None)
                if w is None:
                    raise CampaignConfigError(f"{cid}: replay campaigns need params.window_relative_s")
                params["window_ns"] = [vstart + _ns(w[0]), vstart + _ns(w[1])]       # exact
                params["window_s"] = [params["window_ns"][0] / NS, params["window_ns"][1] / NS]  # readable
                if params.get("source_vehicle", "self") == "self":
                    params["source_vehicle"] = vid
                vend = vstart + (_ns(w[1]) - _ns(w[0])) + _ns(float(params.get("time_offset_ms", 0.0)) / 1e3)
            else:
                if dur is None or float(dur) <= 0:
                    raise CampaignConfigError(f"{cid}: duration_s must be > 0")
                vend = vstart + _ns(dur)
            m = Member(cid, vid, f"{cid}_{vid}", vstart, vend, rel, params)
            if pattern == "controller":
                m.pattern_offset_ns, m.latency_ns = offsets_ns[i], latency_ns[i]
            camp.members.append(m)
        campaigns.append(camp)
    return campaigns


SCENARIOS = {
    "S0": "benign fleet: no attacks, no campaigns",
    "S1": "isolated single-vehicle attack(s): attacks file only, no campaigns",
    "S2": "independent multi-vehicle attacks: >= 2 independent campaigns on >= 2 vehicles, no coordinated campaign",
    "S3": "coordinated multi-vehicle attacks: >= 1 coordinated campaign (independent campaigns may be mixed in)",
}


def check_scenario(scenario_id: str, isolated_attacks: list[dict], campaigns: list[Campaign]) -> list[str]:
    """Return a list of violations of the scenario definition (empty = consistent)."""
    coord = [c for c in campaigns if c.coordinated]
    indep = [c for c in campaigns if not c.coordinated]
    errs = []
    if scenario_id not in SCENARIOS:
        return [f"unknown scenario {scenario_id} (S4 is not generated: weak/strong evidence is a detector property)"]
    if scenario_id == "S0" and (isolated_attacks or campaigns):
        errs.append("S0 must contain no attacks or campaigns")
    if scenario_id == "S1" and (campaigns or not isolated_attacks):
        errs.append("S1 needs >= 1 isolated attack and no campaigns")
    if scenario_id == "S2":
        vehicles = {m.vehicle_id for c in indep for m in c.members}
        if coord or isolated_attacks or len(indep) < 2 or len(vehicles) < 2:
            errs.append("S2 needs >= 2 independent campaigns on >= 2 vehicles and nothing else")
    if scenario_id == "S3" and (not coord or isolated_attacks):
        errs.append("S3 needs >= 1 coordinated campaign and no isolated (campaign-less) attacks")
    return errs


def attack_configs(campaigns: list[Campaign]) -> list[dict]:
    """Per-vehicle attack configurations for the attack engine (exact ns timing)."""
    out = []
    for c in campaigns:
        for m in c.members:
            out.append({"attack_id": m.attack_id, "type": c.attack_type, "vehicle": m.vehicle_id,
                        "campaign_id": c.campaign_id, "start_ns": m.start_ns,
                        "duration_ns": m.end_ns - m.start_ns, "params": m.params})
    return out


def describe(campaigns: list[Campaign]) -> list[dict]:
    return [{"campaign_id": c.campaign_id, "coordinated": c.coordinated, "attack_type": c.attack_type,
             "delay_pattern": c.delay_pattern, "start_ns": c.start_ns, "end_ns": c.end_ns, "seed": c.seed,
             "members": [{"vehicle_id": m.vehicle_id, "attack_id": m.attack_id, "start_ns": m.start_ns,
                          "end_ns": m.end_ns, "relative_delay_ns": m.relative_delay_ns,
                          "params": json.dumps(m.params, sort_keys=True, default=str)} for m in c.members]}
            for c in campaigns]
