"""Load and check the YAML configuration (experiment, fleet, attacks, campaigns)."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .bus_model import BusConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MessageSpec:
    name: str
    period_ms: float
    offset_ms: float
    signals: dict[str, str]          # DBC signal name -> model quantity name


@dataclass(frozen=True)
class VehicleProfile:
    name: str
    dbc: Path
    nominal_bitrate: int
    powertrain: dict[str, Any]
    chassis: dict[str, Any]
    messages: tuple[MessageSpec, ...]
    bus: BusConfig = BusConfig()
    description: str = ""
    roles: dict = field(default_factory=dict)   # semantic role -> (message, signal), e.g. vehicle_speed


@dataclass(frozen=True)
class VehicleAssignment:
    vehicle_id: str
    profile: str
    channel: str
    index: int
    switch_on_ns: int = 0                       # ECU switch-on delay after the vehicle's first SUMO step


ROLES = ("vehicle_speed", "engine_rpm", "accelerator", "brake", "steering")
ASSIGNMENT_STRATEGIES = ("explicit", "round_robin", "balanced_shuffle")


def derive_seed(seed: int, tag: str) -> int:
    """Independent, reproducible sub-seed for one random process (recorded in metadata)."""
    return int(np.random.SeedSequence([int(seed), zlib.crc32(tag.encode())]).generate_state(1)[0])

@dataclass
class ExperimentConfig:
    experiment_id: str
    scenario_id: str
    description: str
    seed: int
    sumo_config: Path
    step_length_s: float
    duration_s: float
    realtime: bool
    transport: str
    bus_model: str               # logical (timing/arbitration model) | ideal (no bus timing)
    frame_format: str
    capture_drain_s: float
    output_root: Path
    output_format: str
    profiles: dict[str, VehicleProfile]
    fleet: list[VehicleAssignment]
    attacks: list[dict] = field(default_factory=list)
    campaigns: list[dict] = field(default_factory=list)
    source_files: dict[str, Path] = field(default_factory=dict)   # name -> path, for snapshots
    tags: dict = field(default_factory=dict)                       # descriptive labels (temporal_pattern, ...)
    dataset_version: str = "unversioned"
    fleet_info: dict = field(default_factory=dict)                 # assignment / switch-on settings and seeds

    @property
    def step_ns(self) -> int:
        return round(self.step_length_s * 1e9)

    @property
    def duration_ns(self) -> int:
        return round(self.duration_s * 1e9)


def _read_yaml(path: Path) -> dict:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def _resolve(p: str | Path, root: Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (root / p)


def assign_channels(vehicles: list[dict], pattern: str) -> list[VehicleAssignment]:
    """Map vehicles to CAN channels. Vehicle i (0-based, list order) -> pattern.format(index=i),
    unless the vehicle entry sets an explicit `channel`. Works for any fleet size."""
    out: list[VehicleAssignment] = []
    seen_ids: set[str] = set()
    seen_channels: set[str] = set()
    for i, v in enumerate(vehicles):
        vid = v.get("id")
        if not vid:
            raise ConfigError(f"fleet vehicle #{i} has no id")
        if vid in seen_ids:
            raise ConfigError(f"duplicate vehicle id {vid}")
        ch = v.get("channel") or pattern.format(index=i)
        if ch in seen_channels:
            raise ConfigError(f"channel {ch} assigned to more than one vehicle")
        seen_ids.add(vid)
        seen_channels.add(ch)
        out.append(VehicleAssignment(vehicle_id=vid, profile=v.get("profile", ""), channel=ch, index=i))
    return out


def build_fleet(section: dict, profiles: dict, seed: int) -> tuple[list[VehicleAssignment], dict]:
    """Vehicles, their profiles and switch-on offsets. Depends only on configuration and seed,
    never on attacks or campaigns. Returns (assignments, info for metadata)."""
    pattern = section.get("channel_pattern", "vcan{index}")
    info: dict = {"channel_pattern": pattern}
    if section.get("vehicles"):
        vehicles = [dict(v) for v in section["vehicles"]]
        info["profile_assignment"] = {"strategy": "explicit"}
    else:
        n = int(section["count"])
        idp = section.get("id_pattern", "vehicle_{n:02d}")
        pa = dict(section.get("profile_assignment") or {})
        strategy = pa.get("strategy", "round_robin")
        names = list(pa.get("profiles") or sorted(profiles))
        if strategy not in ASSIGNMENT_STRATEGIES[1:] or not names:
            raise ConfigError(f"profile_assignment.strategy must be one of {ASSIGNMENT_STRATEGIES[1:]} with profiles")
        cycle = [names[i % len(names)] for i in range(n)]          # balanced counts (differ by <= 1)
        aseed = int(pa["seed"]) if pa.get("seed") is not None else derive_seed(seed, "profile_assignment")
        if strategy == "balanced_shuffle":
            rng = np.random.default_rng(aseed)
            if n < len(names):
                # fewer vehicles than profiles: seeded subset WITHOUT replacement, in random order
                # (N=2 can be A+B, A+C or B+C; a given seed always gives the same pair and order)
                cycle = [names[i] for i in rng.choice(len(names), size=n, replace=False)]
            else:
                cycle = [cycle[i] for i in rng.permutation(n)]
        vehicles = [{"id": idp.format(n=i + 1), "profile": p} for i, p in enumerate(cycle)]
        info["profile_assignment"] = {"strategy": strategy, "profiles": names,
                                      "seed": aseed if strategy == "balanced_shuffle" else None}
    fleet = assign_channels(vehicles, pattern)
    so = dict(section.get("switch_on") or {})
    if so:
        lo, hi = (float(x) for x in so.get("range_s", [0.0, 0.0]))
        res = round(float(so.get("resolution_s", 1e-6)) * 1e9)
        if not (0 <= lo <= hi) or res < 1:
            raise ConfigError("switch_on.range_s must be 0 <= lo <= hi and resolution_s > 0")
        sseed = int(so["seed"]) if so.get("seed") is not None else derive_seed(seed, "switch_on")
        draws = np.random.default_rng(sseed).uniform(lo, hi, size=len(fleet))
        offs = [int(round(d * 1e9 / res)) * res for d in draws]
        fleet = [VehicleAssignment(a.vehicle_id, a.profile, a.channel, a.index, o) for a, o in zip(fleet, offs)]
        info["switch_on"] = {"distribution": "uniform", "range_s": [lo, hi], "resolution_ns": res, "seed": sseed}
    else:
        info["switch_on"] = {"distribution": "none", "range_s": [0.0, 0.0]}
    jit = dict(section.get("ecu_jitter") or {})
    frac = float(jit.get("fraction", 0.0))
    if not 0.0 <= frac < 0.25:
        raise ConfigError("ecu_jitter.fraction must be in [0, 0.25)")
    jseed = int(jit["seed"]) if jit.get("seed") is not None else derive_seed(seed, "ecu_jitter")
    info["ecu_jitter"] = {"model": "uniform_release" if frac > 0 else "none", "fraction": frac,
                          "seed": jseed if frac > 0 else None,
                          "per_stream_seed": "derive_seed(seed, 'ecu_jitter/<vehicle_id>/<message>')" if frac > 0 else None}
    return fleet, info


def _load_profiles(raw: dict, root: Path) -> dict[str, VehicleProfile]:
    profiles = {}
    for name, p in (raw or {}).items():
        msgs = []
        for m in p.get("messages", []):
            period, offset = float(m["period_ms"]), float(m.get("offset_ms", 0.0))
            if period <= 0 or not (0 <= offset < period):
                raise ConfigError(f"profile {name} message {m['name']}: need period>0 and 0<=offset<period")
            msgs.append(MessageSpec(m["name"], period, offset, dict(m.get("signals", {}))))
        if not msgs:
            raise ConfigError(f"profile {name} defines no messages")
        profiles[name] = VehicleProfile(
            name=name,
            dbc=_resolve(p["dbc"], root),
            nominal_bitrate=int(p.get("nominal_bitrate", 500000)),
            powertrain=dict(p.get("powertrain", {})),
            chassis=dict(p.get("chassis", {})),
            messages=tuple(msgs),
            bus=BusConfig(**{"bitrate": int(p.get("nominal_bitrate", 500000)), **(p.get("bus") or {})}),
            description=p.get("description", ""),
            roles={r: tuple(v) for r, v in (p.get("roles") or {}).items()},
        )
        for r, (m, sig) in profiles[name].roles.items():
            spec = next((x for x in msgs if x.name == m), None)
            if r not in ROLES or spec is None or sig not in spec.signals:
                raise ConfigError(f"profile {name}: role {r} -> {m}.{sig} is not a known role/message/signal")
    return profiles


def load_experiment(path: str | Path, root: Path = PROJECT_ROOT) -> ExperimentConfig:
    path = _resolve(path, root)
    raw = _read_yaml(path)
    exp, sim, can, out = (raw.get(k, {}) for k in ("experiment", "simulation", "can", "output"))

    fleet_path = _resolve(raw.get("fleet_config", "config/vehicles.yaml"), root)
    attacks_path = _resolve(raw.get("attacks_config", "config/attacks.yaml"), root)
    campaigns_path = _resolve(raw.get("campaigns_config", "config/campaigns.yaml"), root)
    fleet_raw = _read_yaml(fleet_path)

    prof_raw = dict(fleet_raw.get("profiles") or {})
    profiles_path = None
    if fleet_raw.get("profiles_file"):
        profiles_path = _resolve(fleet_raw["profiles_file"], root)
        prof_raw = {**_read_yaml(profiles_path).get("profiles", {}), **prof_raw}
    profiles = _load_profiles(prof_raw, root)
    fleet, fleet_info = build_fleet(fleet_raw.get("fleet", {}), profiles, int(exp["seed"]))
    if not fleet:
        raise ConfigError("fleet has no vehicles")
    for a in fleet:
        if a.profile not in profiles:
            raise ConfigError(f"{a.vehicle_id}: unknown profile '{a.profile}'")

    cfg = ExperimentConfig(
        experiment_id=str(exp["experiment_id"]),
        scenario_id=str(exp["scenario_id"]),
        description=str(exp.get("description", "")),
        seed=int(exp["seed"]),
        sumo_config=_resolve(sim["sumo_config"], root),
        step_length_s=float(sim["step_length_s"]),
        duration_s=float(sim["duration_s"]),
        realtime=bool(sim.get("realtime", True)),
        transport=str(can.get("transport", "socketcan")),
        bus_model=str(can.get("bus_model", "logical")),
        frame_format=str(can.get("frame_format", "classical")),
        capture_drain_s=float(can.get("capture_drain_s", 0.5)),
        output_root=_resolve(out.get("root", "data"), root),
        output_format=str((raw.get("storage") or {}).get("format", out.get("format", "csv"))),
        profiles=profiles,
        fleet=fleet,
        attacks=list(_read_yaml(attacks_path).get("attacks") or []),
        campaigns=list(_read_yaml(campaigns_path).get("campaigns") or []),
        source_files={"experiment.yaml": path, "vehicles.yaml": fleet_path,
                      "attacks.yaml": attacks_path, "campaigns.yaml": campaigns_path},
        tags=dict(exp.get("tags") or {}),
        dataset_version=str(exp.get("dataset_version", "unversioned")),
        fleet_info=fleet_info,
    )
    if profiles_path is not None:
        cfg.source_files["profiles.yaml"] = profiles_path
    if cfg.step_length_s <= 0 or cfg.duration_s <= 0:
        raise ConfigError("step_length_s and duration_s must be > 0")
    if cfg.bus_model not in ("logical", "ideal"):
        raise ConfigError("can.bus_model must be 'logical' or 'ideal'")
    if cfg.output_format not in ("csv", "parquet"):
        raise ConfigError("storage.format must be 'csv' or 'parquet'")
    if cfg.frame_format != "classical":
        raise ConfigError("only classical CAN is supported")
    for prof in profiles.values():
        cfg.source_files[f"dbc/{prof.dbc.name}"] = prof.dbc
    return cfg
