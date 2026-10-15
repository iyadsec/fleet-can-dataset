"""Attack engine (Milestone 2): Flooding, Fuzzy, Malfunction, Replay against single vehicles.

Rules
  * Attacks are created only from configuration (config/attacks_*.yaml); nothing is hard-coded.
  * Every attack frame is a new TxFrame tagged source=attack_injection (extra frame from an
    ATTACKER node) or source=attack_modification (a legitimate ECU frame altered in place; the
    original payload is kept in frame.ref). Benign generation never sees attack information.
  * Times are on the SUMO timeline. An attack generates frames whose generation time lies in
    [start, end). All attack frames pass through the same CAN bus timing model as benign frames.
  * Randomness: one numpy Generator per attack, seeded from (experiment seed, attack_id).
  * Campaign membership: attacks created by src/campaign_scheduler.py carry their campaign_id;
    attacks from an attacks file (isolated, S1) have none. The engine never assigns campaigns.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field, replace

import numpy as np

from .can_generator import TxFrame, load_dbc

ATTACKER_NODE = "ATTACKER"
TYPES = ("flooding", "fuzzy", "malfunction", "replay")


class AttackConfigError(ValueError):
    pass


def attack_seed(experiment_seed: int, attack_id: str) -> int:
    return int(np.random.SeedSequence([experiment_seed, zlib.crc32(attack_id.encode())]).generate_state(1)[0])


def _payload(spec, dlc: int, rng) -> bytes:
    if spec in (None, "zeros"):
        return bytes(dlc)
    if spec == "random":
        return bytes(rng.integers(0, 256, dlc, dtype=np.uint8).tolist())
    data = bytes.fromhex(str(spec))
    if len(data) != dlc:
        raise AttackConfigError(f"payload {spec} has {len(data)} bytes, dlc is {dlc}")
    return data


def _period_ns(params: dict, default_ns: int | None = None) -> int:
    if "rate_hz" in params:
        rate = float(params["rate_hz"])
        if rate <= 0:
            raise AttackConfigError("rate_hz must be > 0")
        return max(1, round(1e9 / rate))
    if default_ns is None:
        raise AttackConfigError("rate_hz is required")
    return default_ns


@dataclass
class Attack:
    attack_id: str
    attack_type: str
    vehicle_id: str
    channel: str
    start_ns: int
    end_ns: int
    params: dict
    seed: int
    rng: np.random.Generator = field(repr=False, default=None)
    campaign_id: str | None = None
    generated: int = 0
    skipped_inactive: int = 0
    requested_rate_hz: float | None = None

    def _tag(self, sim_time_ns: int, can_id: int, data: bytes, message: str, ref: dict | None = None) -> TxFrame:
        self.generated += 1
        return TxFrame(sim_time_ns, self.vehicle_id, self.channel, can_id, len(data), data, message,
                       source="attack_injection", node=ATTACKER_NODE, attack_id=self.attack_id,
                       attack_type=self.attack_type, campaign_id=self.campaign_id, ref=ref)

    def _grid(self, t0: int, t1: int, period: int, phase_ns: int = 0):
        """Times start + phase + k*period that fall in [t0, t1) and inside the attack window."""
        first = self.start_ns + phase_ns
        lo, hi = max(t0, first), min(t1, self.end_ns)
        if lo >= hi:
            return []
        k = max(0, -(-(lo - first) // period))
        out, t = [], first + k * period
        while t < hi:
            out.append(t)
            t += period
        return out

    # hooks
    def setup(self, ctx: "AttackContext") -> None:
        pass

    def inject(self, t0: int, t1: int, ctx: "AttackContext") -> list[TxFrame]:
        return []

    def modify(self, frame: TxFrame, ctx: "AttackContext") -> TxFrame:
        return frame

    @property
    def params_json(self) -> str:
        return json.dumps(self.params, sort_keys=True, default=str)


class Flooding(Attack):
    def setup(self, ctx):
        p = self.params
        self.can_id = int(p.get("can_id", 0))
        self.dlc = int(p.get("dlc", 8))
        if not (0 <= self.can_id <= 0x7FF and 0 <= self.dlc <= 8):
            raise AttackConfigError(f"{self.attack_id}: invalid can_id/dlc")
        self.period = _period_ns(p)
        self.requested_rate_hz = float(p["rate_hz"])
        self.payload_spec = p.get("payload", "zeros")
        self.fixed = None if self.payload_spec == "random" else _payload(self.payload_spec, self.dlc, None)

    def inject(self, t0, t1, ctx):
        return [self._tag(t, self.can_id, self.fixed if self.fixed is not None else _payload("random", self.dlc, self.rng),
                          "FLOOD", {"payload_mode": self.payload_spec})
                for t in self._grid(t0, t1, self.period)]


class Fuzzy(Attack):
    def setup(self, ctx):
        p = self.params
        self.mode = p.get("mode")
        if self.mode not in ("id", "payload", "both"):
            raise AttackConfigError(f"{self.attack_id}: fuzzy mode must be id | payload | both")
        self.period = _period_ns(p)
        self.requested_rate_hz = float(p["rate_hz"])
        lo, hi = p.get("id_range", [0, 0x7FF])
        self.id_lo, self.id_hi = int(lo), int(hi)
        if not (0 <= self.id_lo <= self.id_hi <= 0x7FF):
            raise AttackConfigError(f"{self.attack_id}: bad id_range")
        self.dlc_spec = p.get("dlc", 8)
        if self.mode == "payload":
            self.can_id = int(p["can_id"])          # fixed ID required
        if self.mode == "id":
            self.fixed_dlc = 8 if self.dlc_spec == "random" else int(self.dlc_spec)
            self.fixed_payload = _payload(p.get("payload", "zeros"), self.fixed_dlc, None)

    def inject(self, t0, t1, ctx):
        out = []
        for t in self._grid(t0, t1, self.period):
            if self.mode == "id":
                cid, data = int(self.rng.integers(self.id_lo, self.id_hi + 1)), self.fixed_payload
            else:
                cid = self.can_id if self.mode == "payload" else int(self.rng.integers(self.id_lo, self.id_hi + 1))
                dlc = int(self.rng.integers(0, 9)) if self.dlc_spec == "random" else int(self.dlc_spec)
                data = _payload("random", dlc, self.rng)
            out.append(self._tag(t, cid, data, "FUZZ", {"fuzzy_mode": self.mode}))
        return out


class Malfunction(Attack):
    """A frame of a real DBC message carrying a wrong value of one signal.
    mode=inject : attacker node sends extra frames of that message (other signals and the
                  counter follow the latest legitimate frame, so only the target signal is wrong).
    mode=replace: the legitimate ECU frames inside the window carry the wrong value (compromised ECU)."""

    def setup(self, ctx):
        p = self.params
        self.mode = p.get("mode", "inject")
        if self.mode not in ("inject", "replace"):
            raise AttackConfigError(f"{self.attack_id}: malfunction mode must be inject | replace")
        prof = ctx.profiles[self.vehicle_id]
        db = load_dbc(prof.dbc)
        try:
            self.msg = db.get_message_by_name(p["message"])
            self.sig = self.msg.get_signal_by_name(p["signal"])
        except KeyError as e:
            raise AttackConfigError(f"{self.attack_id}: {e} not in DBC") from e
        if self.sig.name == "Counter":
            raise AttackConfigError("manipulating the counter is not a semantic malfunction")
        self.manip = dict(p["manipulation"])
        if self.manip.get("strategy") not in ("constant", "offset", "scale", "ramp"):
            raise AttackConfigError(f"{self.attack_id}: manipulation strategy constant|offset|scale|ramp")
        spec = next(m for m in prof.messages if m.name == self.msg.name)
        self.period = _period_ns(p, round(spec.period_ms * 1e6))
        self.requested_rate_hz = 1e9 / self.period
        self.phase_ns = round(float(p.get("phase_ms", 0.0)) * 1e6)

    def malicious_value(self, t: int, expected: float) -> float:
        m, s = self.manip, self.manip["strategy"]
        if s == "constant":
            v = float(m["value"])
        elif s == "offset":
            v = expected + float(m["offset"])
        elif s == "scale":
            v = expected * float(m["factor"])
        else:
            frac = (t - self.start_ns) / max(1, self.end_ns - self.start_ns)
            v = float(m["from"]) + (float(m["to"]) - float(m["from"])) * frac
        lo, hi = self.sig.minimum, self.sig.maximum
        if (lo is not None and v < lo - 1e-9) or (hi is not None and v > hi + 1e-9):
            # Never clamp silently: an unencodable value is a configuration error.
            raise AttackConfigError(f"{self.attack_id}: value {v} outside DBC range [{lo}, {hi}] of {self.sig.name}")
        return v

    def _forge(self, base_data: bytes, t: int, counter: int | None):
        values = self.msg.decode(base_data, decode_choices=False)
        expected = float(values[self.sig.name])
        bad = self.malicious_value(t, expected)
        values[self.sig.name] = bad
        if counter is not None and "Counter" in values:
            values["Counter"] = counter
        data = bytes(self.msg.encode(values, scaling=True, padding=False, strict=True))
        actual = float(self.msg.decode(data, decode_choices=False)[self.sig.name])   # after DBC quantisation
        return data, expected, actual

    def inject(self, t0, t1, ctx):
        if self.mode != "inject":
            return []
        out = []
        for t in self._grid(t0, t1, self.period, self.phase_ns):
            last = ctx.last_benign(self.vehicle_id, self.msg.frame_id, t)
            if last is None:
                continue                                   # nothing legitimate observed yet
            ctr = (self.msg.decode(last.data, decode_choices=False).get("Counter", 0) + 1) % 16
            data, expected, actual = self._forge(last.data, t, int(ctr))
            out.append(self._tag(t, self.msg.frame_id, data, self.msg.name, {
                "message": self.msg.name, "signal": self.sig.name, "expected_value": expected,
                "malicious_value": actual, "reference_payload": last.data.hex().upper(),
                "reference_sim_time_ns": last.sim_time_ns}))
        return out

    def modify(self, frame, ctx):
        if (self.mode != "replace" or frame.vehicle_id != self.vehicle_id or frame.can_id != self.msg.frame_id
                or frame.source != "benign_generator" or not (self.start_ns <= frame.sim_time_ns < self.end_ns)):
            return frame
        data, expected, actual = self._forge(frame.data, frame.sim_time_ns, None)
        self.generated += 1
        return replace(frame, data=data, source="attack_modification", attack_id=self.attack_id,
                       attack_type=self.attack_type, campaign_id=self.campaign_id, ref={
                           "message": self.msg.name, "signal": self.sig.name, "expected_value": expected,
                           "malicious_value": actual, "original_payload": frame.data.hex().upper()})


class Replay(Attack):
    def setup(self, ctx):
        p = self.params
        self.source_vehicle = p.get("source_vehicle", self.vehicle_id)
        if "window_ns" in p:                         # exact window from the campaign scheduler
            self.w0, self.w1 = int(p["window_ns"][0]), int(p["window_ns"][1])
        else:
            w0, w1 = p["window_s"]
            self.w0, self.w1 = round(float(w0) * 1e9), round(float(w1) * 1e9)
        self.offset_ns = round(float(p.get("time_offset_ms", 0.0)) * 1e6)
        ids = p.get("can_ids", "all")
        self.ids = None if ids == "all" else {int(i) for i in ids}
        if not (0 <= self.w0 < self.w1 <= self.start_ns) or self.start_ns - self.w0 < 100_000_000:
            raise AttackConfigError(f"{self.attack_id}: need 0 <= window start < window end <= replay start "
                                    f"and window start >= 100 ms before replay start")
        # Replayed frames are generated at start + (tx_end - w0) + offset with w0 <= tx_end < w1.
        self.end_ns = self.start_ns + (self.w1 - self.w0) + self.offset_ns
        self._queue = None

    def inject(self, t0, t1, ctx):
        if t1 <= self.start_ns or t0 >= self.end_ns:
            return []
        if self._queue is None:
            self._queue, self._idx, self._seen = [], 0, 0
        # Incremental: add frames newly observed on the source bus. A frame needed for replay time
        # < t1 ended before w0 + (t1 - start) <= t0 (start - w0 >= 100 ms > one step), so its bus
        # decision is already final when it is needed.
        seen = ctx.transmitted(self.source_vehicle)
        for tx in seen[self._seen:]:
            if (self.w0 <= tx.end_ns < self.w1 and tx.frame.source == "benign_generator"
                    and (self.ids is None or tx.frame.can_id in self.ids)):
                self._queue.append((self.start_ns + (tx.end_ns - self.w0) + self.offset_ns, tx))
        self._seen = len(seen)
        out = []
        while self._idx < len(self._queue) and self._queue[self._idx][0] < t1:
            t, tx = self._queue[self._idx]
            self._idx += 1
            if t < t0:
                raise AttackConfigError(f"{self.attack_id}: replay frame at {t} ns observed too late")
            o = tx.frame
            out.append(self._tag(t, o.can_id, o.data, o.message, {
                "original_frame": o, "original_sim_time_ns": o.sim_time_ns, "original_tx_end_ns": tx.end_ns,
                "source_vehicle": self.source_vehicle, "target_vehicle": self.vehicle_id,
                "original_payload": o.data.hex().upper()}))
        return out


CLASSES = {"flooding": Flooding, "fuzzy": Fuzzy, "malfunction": Malfunction, "replay": Replay}


class AttackContext:
    """Read-only view of the bus for attackers: what was generated/transmitted so far."""

    def __init__(self, profiles: dict):
        self.profiles = profiles               # vehicle_id -> VehicleProfile
        self._tx: dict[str, list] = {}
        self._last: dict[tuple, list] = {}

    def record_generated(self, frames):
        for f in frames:
            if f.source == "benign_generator":
                hist = self._last.setdefault((f.vehicle_id, f.can_id), [])
                hist.append(f)
                if len(hist) > 8:
                    del hist[:-4]

    def record_transmitted(self, txs):
        for tx in txs:
            self._tx.setdefault(tx.frame.vehicle_id, []).append(tx)

    def transmitted(self, vehicle_id):
        return self._tx.get(vehicle_id, [])

    def last_benign(self, vehicle_id, can_id, t):
        hist = self._last.get((vehicle_id, can_id), [])
        for f in reversed(hist[-4:]):
            if f.sim_time_ns <= t:
                return f
        return None


class AttackEngine:
    def __init__(self, attack_cfgs: list[dict], fleet, profiles_by_vehicle: dict, experiment_seed: int,
                 campaign_ids: set[str] | None = None):
        self.ctx = AttackContext(profiles_by_vehicle)
        self.transmitted: dict[str, int] = {}
        chan = {a.vehicle_id: a.channel for a in fleet}
        self.attacks: list[Attack] = []
        seen = set()
        for c in attack_cfgs:
            aid, typ, vid = c.get("attack_id"), c.get("type"), c.get("vehicle")
            if not aid or aid in seen:
                raise AttackConfigError(f"missing or duplicate attack_id {aid}")
            if typ not in TYPES:
                raise AttackConfigError(f"{aid}: unknown type {typ}")
            if vid not in chan:
                raise AttackConfigError(f"{aid}: unknown vehicle {vid}")
            cid = c.get("campaign_id") or None
            if cid is not None and cid not in (campaign_ids or set()):
                raise AttackConfigError(f"{aid}: campaign {cid} is not defined by the campaign scheduler")
            seen.add(aid)
            start = int(c["start_ns"]) if "start_ns" in c else round(float(c["start_s"]) * 1e9)
            end = start + (int(c["duration_ns"]) if "duration_ns" in c else round(float(c.get("duration_s", 0)) * 1e9))
            seed = attack_seed(experiment_seed, aid)
            a = CLASSES[typ](aid, typ, vid, chan[vid], start, end, dict(c.get("params") or {}), seed,
                             np.random.default_rng(seed), campaign_id=cid)
            a.setup(self.ctx)
            if a.end_ns <= a.start_ns:
                raise AttackConfigError(f"{aid}: duration must be > 0")
            self.attacks.append(a)

    def process(self, frames: list[TxFrame], t0: int, t1: int, active: set[str]) -> list[TxFrame]:
        self.ctx.record_generated(frames)
        if not self.attacks:
            return frames
        out = []
        for f in frames:
            for a in self.attacks:
                f = a.modify(f, self.ctx)
            out.append(f)
        for a in self.attacks:
            injected = a.inject(t0, t1, self.ctx)
            if a.vehicle_id not in active:
                a.skipped_inactive += len(injected)
                a.generated -= len(injected)
                continue
            out.extend(injected)
        out.sort(key=lambda fr: (fr.sim_time_ns, fr.vehicle_id, fr.can_id))
        return out

    def observe(self, txs) -> None:
        self.ctx.record_transmitted(txs)
        for tx in txs:
            if tx.frame.attack_id:
                self.transmitted[tx.frame.attack_id] = self.transmitted.get(tx.frame.attack_id, 0) + 1

    def describe(self) -> list[dict]:
        return [{"attack_id": a.attack_id, "attack_type": a.attack_type, "vehicle_id": a.vehicle_id,
                 "campaign_id": a.campaign_id,
                 "channel": a.channel, "start_ns": a.start_ns, "end_ns": a.end_ns, "seed": a.seed,
                 "requested_rate_hz": a.requested_rate_hz, "generated": a.generated,
                 "skipped_inactive": a.skipped_inactive, "params": a.params_json}
                for a in self.attacks]


def build_attacks(attack_configs, fleet=None, profiles_by_vehicle=None, seed=0, campaign_ids=None) -> AttackEngine:
    return AttackEngine(attack_configs or [], fleet or [], profiles_by_vehicle or {}, seed, campaign_ids)
