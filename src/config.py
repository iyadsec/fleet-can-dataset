"""Load and check the YAML configuration (experiment, fleet, attacks, campaigns)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class VehicleAssignment:
    vehicle_id: str
    profile: str
    channel: str
    index: int


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
        )
    return profiles


def load_experiment(path: str | Path, root: Path = PROJECT_ROOT) -> ExperimentConfig:
    path = _resolve(path, root)
    raw = _read_yaml(path)
    exp, sim, can, out = (raw.get(k, {}) for k in ("experiment", "simulation", "can", "output"))

    fleet_path = _resolve(raw.get("fleet_config", "config/vehicles.yaml"), root)
    attacks_path = _resolve(raw.get("attacks_config", "config/attacks.yaml"), root)
    campaigns_path = _resolve(raw.get("campaigns_config", "config/campaigns.yaml"), root)
    fleet_raw = _read_yaml(fleet_path)

    profiles = _load_profiles(fleet_raw.get("profiles", {}), root)
    fleet_section = fleet_raw.get("fleet", {})
    fleet = assign_channels(fleet_section.get("vehicles", []), fleet_section.get("channel_pattern", "vcan{index}"))
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
        output_format=str(out.get("format", "csv")),
        profiles=profiles,
        fleet=fleet,
        attacks=list(_read_yaml(attacks_path).get("attacks") or []),
        campaigns=list(_read_yaml(campaigns_path).get("campaigns") or []),
        source_files={"experiment.yaml": path, "vehicles.yaml": fleet_path,
                      "attacks.yaml": attacks_path, "campaigns.yaml": campaigns_path},
    )
    if cfg.step_length_s <= 0 or cfg.duration_s <= 0:
        raise ConfigError("step_length_s and duration_s must be > 0")
    if cfg.bus_model not in ("logical", "ideal"):
        raise ConfigError("can.bus_model must be 'logical' or 'ideal'")
    if cfg.frame_format != "classical":
        raise ConfigError("only classical CAN is supported")
    for prof in profiles.values():
        cfg.source_files[f"dbc/{prof.dbc.name}"] = prof.dbc
    return cfg
