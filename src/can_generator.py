"""Per-vehicle synthetic CAN generator: SUMO VehicleState -> signals -> DBC-encoded frames.

Every step is documented in docs/timing_model.md and docs/dataset_schema.md. Summary:
  1. SyntheticVehicleModel maps SUMO mobility to physical quantities (RPM, gear, pedal, brake,
     steering). These are SYNTHETIC modelling assumptions, not measured vehicle behaviour.
  2. Each DBC message is an ECU task with a fixed period and phase offset (from the profile).
     A frame is due at  t = ignition_time + offset + k * period  on the SUMO time line.
  3. Signals are sampled-and-held: a frame due inside step window [t_k, t_k + step) carries the
     state SUMO reported at t_k.
  4. Payloads are encoded with cantools from the profile's DBC. No other bytes are written.
The generator never sees labels, attacks or campaign information.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cantools

from .config import MessageSpec, VehicleProfile
from .sumo_controller import VehicleState


@dataclass(frozen=True)
class TxFrame:
    """A frame scheduled for transmission. Ground-truth side: carries its SUMO time."""
    sim_time_ns: int
    vehicle_id: str
    channel: str
    can_id: int
    dlc: int
    data: bytes
    message: str
    source: str = "benign_generator"     # provenance of the frame (attacks will use other values)


@lru_cache(maxsize=None)
def load_dbc(path: Path) -> cantools.database.can.Database:
    return cantools.database.load_file(str(path), strict=True)


class SyntheticVehicleModel:
    """Derive powertrain/chassis quantities from SUMO kinematics (documented assumptions)."""

    def __init__(self, powertrain: dict, chassis: dict):
        self.p, self.c = powertrain, chassis
        self._prev_angle: float | None = None
        self._prev_t_ns: int | None = None

    def gear(self, speed_kmh: float) -> int:
        g = 1
        for threshold in self.p["upshift_speeds_kmh"]:
            if speed_kmh >= threshold:
                g += 1
        return min(g, len(self.p["gear_ratios"]))

    def quantities(self, s: VehicleState) -> dict[str, float]:
        p, c = self.p, self.c
        v = max(s.speed_mps, 0.0)
        speed_kmh = v * 3.6
        gear = self.gear(speed_kmh)
        wheel_rpm = v / p["wheel_radius_m"] * 60.0 / (2 * math.pi)
        rpm = max(p["idle_rpm"], wheel_rpm * p["gear_ratios"][gear - 1] * p["final_drive"])

        a = s.accel_mps2
        if v < 0.1 and a <= 0:
            pedal = 0.0
        elif a >= 0:
            pedal = p["pedal_cruise_pct"] + (100 - p["pedal_cruise_pct"]) * a / p["max_accel_mps2"]
        else:
            pedal = 0.0
        pedal = min(max(pedal, 0.0), 100.0)
        brake_active = 1 if a < -p["brake_threshold_mps2"] else 0
        brake_decel = max(-a, 0.0) if brake_active else 0.0

        # Yaw rate from change in SUMO heading; steering from kinematic bicycle model.
        yaw_rate_dps = 0.0
        if self._prev_angle is not None and s.sim_time_ns > self._prev_t_ns:
            d = (s.angle_deg - self._prev_angle + 180.0) % 360.0 - 180.0
            yaw_rate_dps = -d / ((s.sim_time_ns - self._prev_t_ns) / 1e9)  # SUMO angle is clockwise
        self._prev_angle, self._prev_t_ns = s.angle_deg, s.sim_time_ns
        steer = 0.0
        if v > 0.5:
            steer = math.degrees(math.atan(c["wheelbase_m"] * math.radians(yaw_rate_dps) / v)) * c["steering_ratio"]

        return {
            "speed_kmh": speed_kmh, "accel_mps2": a, "engine_rpm": rpm, "gear": gear,
            "pedal_pct": pedal, "brake_active": brake_active, "brake_decel_mps2": brake_decel,
            "yaw_rate_dps": yaw_rate_dps, "steering_angle_deg": steer,
        }


@dataclass
class _Task:
    spec: MessageSpec
    msg: object                 # cantools Message
    period_ns: int
    offset_ns: int
    next_due_ns: int | None = None
    counter: int = 0


def _clamp_to_signal(sig, value: float) -> float:
    lo = sig.minimum if sig.minimum is not None else -math.inf
    hi = sig.maximum if sig.maximum is not None else math.inf
    return min(max(value, lo), hi)


@dataclass
class VehicleCanGenerator:
    vehicle_id: str
    channel: str
    profile: VehicleProfile
    tasks: list[_Task] = field(init=False)
    model: SyntheticVehicleModel = field(init=False)
    clamped: int = field(init=False, default=0)    # count of values clamped to DBC range

    def __post_init__(self):
        db = load_dbc(self.profile.dbc)
        self.model = SyntheticVehicleModel(self.profile.powertrain, self.profile.chassis)
        self.tasks = []
        for spec in self.profile.messages:
            msg = db.get_message_by_name(spec.name)
            if msg.is_extended_frame or msg.length > 8 or msg.frame_id > 0x7FF:
                raise ValueError(f"{spec.name}: not a classical 11-bit frame")
            missing = set(spec.signals) ^ {s.name for s in msg.signals}
            if missing:
                raise ValueError(f"{spec.name}: profile/DBC signal mismatch {sorted(missing)}")
            self.tasks.append(_Task(spec, msg, round(spec.period_ms * 1e6), round(spec.offset_ms * 1e6)))

    def frames_for_step(self, state: VehicleState, step_ns: int) -> list[TxFrame]:
        """Frames due in [state.sim_time_ns, state.sim_time_ns + step_ns), in time order."""
        t0, t1 = state.sim_time_ns, state.sim_time_ns + step_ns
        q = self.model.quantities(state)
        out: list[TxFrame] = []
        for task in self.tasks:
            if task.next_due_ns is None:               # first step this vehicle exists ("ignition")
                task.next_due_ns = t0 + task.offset_ns
            while task.next_due_ns < t1:
                values = {}
                for sig_name, quantity in task.spec.signals.items():
                    sig = task.msg.get_signal_by_name(sig_name)
                    raw = task.counter if quantity == "counter" else q[quantity]
                    val = _clamp_to_signal(sig, raw)
                    self.clamped += val != raw
                    values[sig_name] = val
                data = task.msg.encode(values, scaling=True, padding=True, strict=True)
                out.append(TxFrame(task.next_due_ns, self.vehicle_id, self.channel,
                                   task.msg.frame_id, len(data), bytes(data), task.spec.name))
                task.counter = (task.counter + 1) % 16
                task.next_due_ns += task.period_ns
        out.sort(key=lambda f: (f.sim_time_ns, f.can_id))
        return out
