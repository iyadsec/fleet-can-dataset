"""Per-vehicle synthetic CAN generator: SUMO VehicleState -> signals -> DBC-encoded frames.

Every step is documented in docs/timing_model.md and docs/dataset_schema.md. Summary:
  1. SyntheticVehicleModel maps SUMO mobility to physical quantities (RPM, gear, pedal, brake,
     steering). These are SYNTHETIC modelling assumptions, not measured vehicle behaviour.
  2. Each DBC message is an ECU task with a fixed period and phase offset (from the profile).
     A frame is due at  t = ignition_time + offset + k * period  on the SUMO time line.
  3. Signals are sampled-and-held: a frame due inside step window [t_k, t_k + step) carries the
     state SUMO reported at t_k.
  4. Payloads are encoded with cantools from the profile's DBC; unused bits are 0.
The generator never sees labels, attacks or campaign information.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cantools
import numpy as np

from .config import MessageSpec, VehicleProfile, derive_seed
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
    source: str = "benign_generator"     # provenance: benign_generator | attack_injection | attack_modification
    node: str = ""                       # transmitting node on the vehicle bus (DBC sender or ATTACKER)
    attack_id: str | None = None
    attack_type: str | None = None
    campaign_id: str | None = None       # set only for attacks created by the campaign scheduler
    ref: dict | None = field(default=None, compare=False, hash=False)   # attack ground-truth details


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


class ExtendedVehicleModel:
    """Generator 2.0.0: additional SYNTHETIC body/powertrain quantities for the richer profiles D-H.

    Deterministic functions of the SUMO state plus per-vehicle state seeded from `seed` (initial temperatures, fuel,
    odometer, wheel-radius tolerances, headlight state). Every quantity is a documented modelling assumption, not a
    measurement. Profiles A-C never reference these quantities, so their frames are unchanged.
      lat_accel_mps2      v * yaw rate
      wheel_{fl,fr,rl,rr}_kmh  speed * (1 + tolerance_i) -/+ yaw-rate differential (track 1.6 m)
      engine_torque_nm    max_torque * pedal/100 * rpm shape (peak at 3500 rpm), 0 below 1 % pedal
      throttle_pct        5 + 0.9 * pedal
      coolant_temp_c      first-order warm-up towards 90 C (tau 180 s, faster under load)
      oil_temp_c          first-order towards coolant + 10 C (tau 400 s)
      ambient_temp_c      constant per vehicle, U[-5, 35] C
      battery_v           14.1 V while running, -0.25 V with headlights, -0.002 V/A of torque-related load
      fuel_pct            initial U[8, 98] %, decreasing with torque * rpm
      odometer_km         initial U[500, 150000] km + distance driven
      turn_signal         0 off, 1 left, 2 right; on above +-6 deg/s yaw, off below +-2 deg/s (hysteresis)
      headlight           per-vehicle constant (P = 0.35 on)
      door_locked         0 until speed first exceeds 15 km/h, then 1
    """

    def __init__(self, powertrain: dict, seed: int):
        self.p = powertrain
        rng = np.random.default_rng(seed)
        self.tol = rng.normal(0.0, 0.003, size=4)
        self.coolant = float(rng.uniform(15.0, 92.0))
        self.oil = self.coolant + float(rng.uniform(-5.0, 5.0))
        self.ambient = float(rng.uniform(-5.0, 35.0))
        self.fuel = float(rng.uniform(8.0, 98.0))
        self.odo = float(rng.uniform(500.0, 150000.0))
        self.headlight = int(rng.random() < 0.35)
        self.turn = 0
        self.locked = 0
        self._t = None

    def quantities(self, s: VehicleState, base: dict) -> dict[str, float]:
        dt = 0.0 if self._t is None else (s.sim_time_ns - self._t) / 1e9
        self._t = s.sim_time_ns
        v = max(s.speed_mps, 0.0)
        yaw = math.radians(base["yaw_rate_dps"])
        diff = yaw * 0.8 * 3.6                                    # half track 0.8 m, km/h
        sp = base["speed_kmh"]
        wheels = [sp * (1 + self.tol[0]) - diff, sp * (1 + self.tol[1]) + diff,
                  sp * (1 + self.tol[2]) - diff, sp * (1 + self.tol[3]) + diff]
        rpm, pedal = base["engine_rpm"], base["pedal_pct"]
        shape = max(0.0, 1.0 - ((rpm - 3500.0) / 4000.0) ** 2)
        torque = self.p.get("max_torque_nm", 250.0) * pedal / 100.0 * shape if pedal >= 1.0 else 0.0
        load = 1.0 + torque / 100.0
        self.coolant += (90.0 - self.coolant) * min(1.0, dt * load / 180.0)
        self.oil += (self.coolant + 10.0 - self.oil) * min(1.0, dt / 400.0)
        self.fuel = max(0.0, self.fuel - dt * (0.0004 + torque * rpm * 2e-9))
        self.odo += v * dt / 1000.0
        if self.turn == 0 and abs(base["yaw_rate_dps"]) > 6.0:
            self.turn = 1 if base["yaw_rate_dps"] > 0 else 2
        elif self.turn and abs(base["yaw_rate_dps"]) < 2.0:
            self.turn = 0
        if sp > 15.0:
            self.locked = 1
        return {
            "lat_accel_mps2": v * yaw, "wheel_fl_kmh": max(0.0, wheels[0]), "wheel_fr_kmh": max(0.0, wheels[1]),
            "wheel_rl_kmh": max(0.0, wheels[2]), "wheel_rr_kmh": max(0.0, wheels[3]), "engine_torque_nm": torque,
            "throttle_pct": 5.0 + 0.9 * pedal, "coolant_temp_c": self.coolant, "oil_temp_c": self.oil,
            "ambient_temp_c": self.ambient, "battery_v": 14.1 - 0.25 * self.headlight - 0.002 * torque,
            "fuel_pct": self.fuel, "odometer_km": self.odo, "turn_signal": self.turn, "headlight": self.headlight,
            "door_locked": self.locked,
        }


NOISE_FREE = {"speed_kmh"}          # vehicle_speed stays exact: validator V17 compares it with SUMO
EXTENDED_QUANTITIES = {"lat_accel_mps2", "wheel_fl_kmh", "wheel_fr_kmh", "wheel_rl_kmh", "wheel_rr_kmh", "engine_torque_nm",
                       "throttle_pct", "coolant_temp_c", "oil_temp_c", "ambient_temp_c", "battery_v", "fuel_pct",
                       "odometer_km", "turn_signal", "headlight", "door_locked"}


@dataclass
class _Task:
    spec: MessageSpec
    msg: object                 # cantools Message
    period_ns: int
    offset_ns: int
    node: str
    next_due_ns: int | None = None             # nominal (unjittered) slot of the next frame
    next_emit_ns: int | None = None            # release time of the next frame (= nominal + jitter)
    counter: int = 0
    rng: object = None                         # numpy Generator for this stream's jitter (None = no jitter)
    event_rng: object = None                   # event-reaction delays (event / mixed messages)
    last_watch: tuple | None = None            # watched quantities at the last emitted frame
    last_emit_ns: int | None = None
    pending_event_ns: int | None = None        # an event detected but not yet sent (minimum interval)


def _clamp_to_signal(sig, value: float) -> float:
    lo = sig.minimum if sig.minimum is not None else -math.inf
    hi = sig.maximum if sig.maximum is not None else math.inf
    return min(max(value, lo), hi)


EVENT_DELAY_NS = (100_000, 2_000_000)          # ECU reaction to a state change: U[0.1, 2] ms (generator 2.0.0)


@dataclass
class VehicleCanGenerator:
    vehicle_id: str
    channel: str
    profile: VehicleProfile
    switch_on_ns: int = 0                          # ECU switch-on delay after the first SUMO step (seeded)
    jitter_fraction: float = 0.0                   # ECU release jitter: uniform +-fraction x period (0 = off)
    jitter_seed: int = 0                           # per-vehicle jitter seed; streams derive their own seed
    model_seed: int = 0                            # generator 2.0.0: extended model state and sensor noise
    tasks: list[_Task] = field(init=False)
    model: SyntheticVehicleModel = field(init=False)
    clamped: int = field(init=False, default=0)    # count of values clamped to DBC range

    def __post_init__(self):
        db = load_dbc(self.profile.dbc)
        self.model = SyntheticVehicleModel(self.profile.powertrain, self.profile.chassis)
        if self.profile.jitter_fraction is not None:   # per-profile release jitter overrides the fleet setting
            self.jitter_fraction = float(self.profile.jitter_fraction)
        quantities = {q for spec in self.profile.messages for q in spec.signals.values()}
        self.extended = None
        if quantities & EXTENDED_QUANTITIES:
            self.extended = ExtendedVehicleModel(self.profile.powertrain, derive_seed(self.model_seed, f"vehicle_model/{self.vehicle_id}"))
        self.noise = {q: float(s) for q, s in (self.profile.noise or {}).items() if q not in NOISE_FREE}
        self.noise_rng = np.random.default_rng(derive_seed(self.model_seed, f"sensor_noise/{self.vehicle_id}")) if self.noise else None
        self.tasks = []
        for spec in self.profile.messages:
            msg = db.get_message_by_name(spec.name)
            if msg.is_extended_frame or msg.length > 8 or msg.frame_id > 0x7FF:
                raise ValueError(f"{spec.name}: not a classical 11-bit frame")
            missing = set(spec.signals) ^ {s.name for s in msg.signals}
            if missing:
                raise ValueError(f"{spec.name}: profile/DBC signal mismatch {sorted(missing)}")
            node = msg.senders[0] if msg.senders else "ECU"
            rng = None
            if self.jitter_fraction > 0:                # independent stream per (vehicle, message)
                rng = np.random.default_rng(derive_seed(self.jitter_seed, f"ecu_jitter/{self.vehicle_id}/{spec.name}"))
            ev = None
            if spec.mode != "periodic":
                ev = np.random.default_rng(derive_seed(self.jitter_seed, f"ecu_event/{self.vehicle_id}/{spec.name}"))
            self.tasks.append(_Task(spec, msg, round(spec.period_ms * 1e6), round(spec.offset_ms * 1e6), node, rng=rng,
                                    event_rng=ev))

    def _jittered(self, task: "_Task") -> int:
        """Release time of the task's current nominal slot: nominal + U(-f, +f) x period (no accumulation,
        so the long-run period equals the nominal period; f < 0.25 keeps per-stream order monotonic)."""
        if task.rng is None:
            return task.next_due_ns
        return task.next_due_ns + int(round(task.rng.uniform(-self.jitter_fraction, self.jitter_fraction) * task.period_ns))

    def _encode(self, task: "_Task", q: dict, t: int) -> TxFrame:
        values = {}
        for sig_name, quantity in task.spec.signals.items():
            sig = task.msg.get_signal_by_name(sig_name)
            raw = task.counter if quantity == "counter" else q[quantity]
            val = _clamp_to_signal(sig, raw)
            self.clamped += val != raw
            values[sig_name] = val
        data = task.msg.encode(values, scaling=True, padding=False, strict=True)   # unused bits = 0
        task.counter = (task.counter + 1) % 16
        task.last_emit_ns = t
        return TxFrame(t, self.vehicle_id, self.channel, task.msg.frame_id, len(data), bytes(data), task.spec.name,
                       node=task.node)

    def frames_for_step(self, state: VehicleState, step_ns: int) -> list[TxFrame]:
        """Frames due in [state.sim_time_ns, state.sim_time_ns + step_ns), in time order.

        periodic : t = ignition + offset + k * period (+ jitter)                     (generator 1.x)
        event    : a frame whenever a watched quantity changes by more than its threshold, sent after an ECU
                   reaction delay U[0.1, 2] ms and at least min_interval after the previous frame; plus a
                   heartbeat every period after the last frame (generator 2.0.0)
        mixed    : periodic grid plus event frames (generator 2.0.0)"""
        t0, t1 = state.sim_time_ns, state.sim_time_ns + step_ns
        q = self.model.quantities(state)
        if self.extended is not None:
            q.update(self.extended.quantities(state, q))
        clean = q
        if self.noise_rng is not None:
            q = dict(q)
            for name in sorted(self.noise):
                if name in q:
                    q[name] = q[name] + float(self.noise_rng.normal(0.0, self.noise[name]))
        out: list[TxFrame] = []
        for task in self.tasks:
            spec = task.spec
            if task.next_due_ns is None:               # first step this vehicle exists ("ignition")
                task.next_due_ns = t0 + self.switch_on_ns + task.offset_ns
                task.next_emit_ns = max(t0, self._jittered(task))   # cannot transmit before the vehicle exists
            if spec.mode != "periodic" and task.last_emit_ns is not None and task.pending_event_ns is None:
                watch = tuple(clean[w] for w in spec.watch)
                if any(abs(a - b) > spec.change_threshold for a, b in zip(watch, task.last_watch)):
                    d = int(task.event_rng.integers(EVENT_DELAY_NS[0], EVENT_DELAY_NS[1] + 1))
                    task.pending_event_ns = max(t0 + d, task.last_emit_ns + round(spec.min_interval_ms * 1e6))
            while True:
                tp = task.next_emit_ns if task.next_emit_ns < t1 else None
                te = task.pending_event_ns if task.pending_event_ns is not None and task.pending_event_ns < t1 else None
                if tp is None and te is None:
                    break
                if te is not None and (tp is None or te < tp):
                    out.append(self._encode(task, q, te))
                    task.pending_event_ns = None
                    if spec.mode == "event":           # heartbeat restarts after an event frame
                        task.next_due_ns = te + task.period_ns
                        task.next_emit_ns = max(te + 1, self._jittered(task))
                else:
                    out.append(self._encode(task, q, tp))
                    task.next_due_ns += task.period_ns       # nominal grid never drifts
                    task.next_emit_ns = self._jittered(task)
                if spec.mode != "periodic":
                    task.last_watch = tuple(clean[w] for w in spec.watch)
        out.sort(key=lambda f: (f.sim_time_ns, f.can_id))
        return out
