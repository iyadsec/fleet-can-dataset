"""Logical CAN bus timing / arbitration model (one instance per vehicle bus).

Linux vCAN delivers frames instantly and has no notion of bitrate or contention. This layer
decides, on the SUMO timeline, WHEN each frame occupies the bus before it is handed to vCAN.

MODELLED
  * Frame duration from the exact Classical CAN bit count at the configured bitrate:
    SOF, 11-bit ID, RTR, IDE, r0, DLC, data, CRC-15 (computed), with bit stuffing applied to
    SOF..CRC, plus CRC delimiter, ACK slot+delimiter, EOF (7) and intermission (3).
  * Bus availability: one frame at a time; the next arbitration happens when the bus is free.
  * Arbitration: among frames ready when the bus becomes free, the lowest CAN ID wins.
    Each node offers its own highest-priority (or oldest, if configured) pending frame.
  * Queueing per node (ECU / attacker) with a configurable limit and drop policy.
  * Frame expiry (optional): with expiry_policy=deadline a frame that has not WON arbitration
    within max_frame_age_ms of its generation is dropped as `expired_drop` at exactly
    generation + max_frame_age. expiry_policy=retain (default, Milestone 2 behaviour) keeps
    every queued frame until it is transmitted, however stale.
  * Bus utilization (busy time / elapsed time), queueing delay, drops.

NOT MODELLED (documented limitations)
  * Bit-level arbitration races, synchronisation, sample point, propagation delay.
  * Error frames, retransmission after errors, error counters, bus-off, overload frames.
  * Two nodes sending the SAME ID at the same time (real CAN: bit error). Here they are
    serialised by ready time and node order, and counted as `same_id_contention`.
  * Electrical/physical layer, transceiver behaviour, ACK failures, CAN FD, remote frames.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field

FIXED_TAIL_BITS = 1 + 2 + 7 + 3          # CRC delimiter, ACK slot + delimiter, EOF, intermission
CRC15_POLY = 0x4599


@dataclass(frozen=True)
class BusConfig:
    bitrate: int = 500_000
    stuffing: str = "exact"              # exact | worst_case | none
    queue_limit: int = 32                # max frames waiting per node
    drop_policy: str = "drop_newest"     # drop_newest (reject arrival) | drop_oldest (evict oldest)
    node_queue_order: str = "priority"   # priority (lowest ID first) | fifo
    enabled: bool = True                 # False: ideal bus, frame "transmitted" at its ready time
    expiry_policy: str = "retain"        # retain (never expire) | deadline (drop after max_frame_age_ms)
    max_frame_age_ms: float | None = None

    def __post_init__(self):
        if self.expiry_policy not in ("retain", "deadline"):
            raise ValueError(f"expiry_policy {self.expiry_policy}")
        if self.expiry_policy == "deadline" and not (self.max_frame_age_ms and self.max_frame_age_ms > 0):
            raise ValueError("expiry_policy=deadline needs max_frame_age_ms > 0")
        if self.bitrate <= 0 or self.queue_limit < 1:
            raise ValueError("bitrate must be > 0 and queue_limit >= 1")
        if self.stuffing not in ("exact", "worst_case", "none"):
            raise ValueError(f"stuffing {self.stuffing}")
        if self.drop_policy not in ("drop_newest", "drop_oldest"):
            raise ValueError(f"drop_policy {self.drop_policy}")
        if self.node_queue_order not in ("priority", "fifo"):
            raise ValueError(f"node_queue_order {self.node_queue_order}")

    @property
    def bit_time_ns(self) -> float:
        return 1e9 / self.bitrate

    @property
    def max_age_ns(self) -> int | None:
        return round(self.max_frame_age_ms * 1e6) if self.expiry_policy == "deadline" else None


def _bits(value: int, width: int) -> list[int]:
    return [(value >> (width - 1 - i)) & 1 for i in range(width)]


def crc15(bits: list[int]) -> int:
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= CRC15_POLY
    return crc


def count_stuff_bits(bits: list[int]) -> int:
    """Stuff bits inserted after every 5 consecutive identical bits (stuff bits count toward runs)."""
    stuffed, run_bit, run_len = 0, None, 0
    for b in bits:
        if b == run_bit:
            run_len += 1
        else:
            run_bit, run_len = b, 1
        if run_len == 5:
            stuffed += 1
            run_bit, run_len = 1 - b, 1      # the inserted complement bit starts a new run
    return stuffed


def frame_bits(can_id: int, data: bytes, stuffing: str = "exact") -> int:
    """Total bits a Classical CAN standard data frame occupies, including intermission."""
    if not 0 <= can_id <= 0x7FF or len(data) > 8:
        raise ValueError("classical 11-bit frames with <= 8 data bytes only")
    head = [0] + _bits(can_id, 11) + [0, 0, 0] + _bits(len(data), 4)
    for byte in data:
        head += _bits(byte, 8)
    stuffable = head + _bits(crc15(head), 15)                   # SOF .. CRC: 34 + 8n bits
    if stuffing == "exact":
        stuff = count_stuff_bits(stuffable)
    elif stuffing == "worst_case":
        stuff = (len(stuffable) - 1) // 4
    else:
        stuff = 0
    return len(stuffable) + stuff + FIXED_TAIL_BITS


@dataclass
class BusTx:
    frame: object               # TxFrame
    node: str
    ready_ns: int               # generation time (frame.sim_time_ns)
    start_ns: int               # modelled transmission start (won arbitration)
    end_ns: int                 # modelled end of frame incl. intermission
    bits: int

    @property
    def queue_delay_ns(self) -> int:
        return self.start_ns - self.ready_ns


@dataclass
class BusDrop:
    frame: object
    node: str
    ready_ns: int
    drop_ns: int
    reason: str

    @property
    def kind(self) -> str:
        return "expired_drop" if self.reason.startswith("expired") else "queue_drop"


@dataclass
class BusStats:
    transmitted: int = 0
    dropped: int = 0
    queue_drops: int = 0
    expired_drops: int = 0
    busy_ns: int = 0
    same_id_contention: int = 0
    max_queue_len: dict = field(default_factory=dict)


class CanBusModel:
    """Discrete-event model. Feed frames with submit(); call advance_until(t) once all frames
    ready before t have been submitted. Decisions are made only for arbitration times < t."""

    def __init__(self, cfg: BusConfig, name: str = "bus"):
        self.cfg, self.name = cfg, name
        self.now_ns = 0                       # time the bus becomes free
        self._arrivals: list = []             # heap (ready, seq, frame, node)
        self._queues: dict[str, list] = {}    # node -> heap of (key, seq, ready, frame)
        self._node_order: dict[str, int] = {}
        self._seq = itertools.count()
        self.stats = BusStats()
        self._horizon = 0                     # arbitration decisions before this time are final

    def submit(self, frame, node: str) -> None:
        ready = frame.sim_time_ns
        if ready < self._horizon:
            # It could have changed an arbitration that was already decided.
            raise ValueError(f"{self.name}: frame ready at {ready} ns submitted after decisions up to {self._horizon} ns")
        self._node_order.setdefault(node, len(self._node_order))
        heapq.heappush(self._arrivals, (ready, next(self._seq), frame, node))

    def _drop(self, drops: list, d: BusDrop) -> None:
        drops.append(d)
        self.stats.dropped += 1
        if d.kind == "expired_drop":
            self.stats.expired_drops += 1
        else:
            self.stats.queue_drops += 1

    def _expire(self, node: str, t: int, drops: list) -> None:
        """Deadline policy: remove frames of `node` whose age at time t exceeds max age."""
        max_age = self.cfg.max_age_ns
        q = self._queues.get(node)
        if max_age is None or not q:
            return
        stale = [e for e in q if t - e[2] > max_age]
        if stale:
            for e in sorted(stale, key=lambda e: (e[2], e[1])):
                self._drop(drops, BusDrop(e[3], node, e[2], e[2] + max_age, "expired_max_age"))
            q[:] = [e for e in q if t - e[2] <= max_age]
            heapq.heapify(q)

    def _enqueue(self, ready, seq, frame, node, drops: list) -> None:
        self._expire(node, ready, drops)          # occupancy seen by an arriving frame excludes expired ones
        q = self._queues.setdefault(node, [])
        if len(q) >= self.cfg.queue_limit:
            if self.cfg.drop_policy == "drop_newest":
                self._drop(drops, BusDrop(frame, node, ready, ready, "queue_full_drop_newest"))
                return
            oldest = min(q, key=lambda e: (e[2], e[1]))
            q.remove(oldest)
            heapq.heapify(q)
            self._drop(drops, BusDrop(oldest[3], node, oldest[2], ready, "queue_full_drop_oldest"))
        key = frame.can_id if self.cfg.node_queue_order == "priority" else ready
        heapq.heappush(q, (key, seq, ready, frame))
        self.stats.max_queue_len[node] = max(self.stats.max_queue_len.get(node, 0), len(q))

    def _admit_until(self, t: int, drops: list) -> None:
        while self._arrivals and self._arrivals[0][0] <= t:
            ready, seq, frame, node = heapq.heappop(self._arrivals)
            self._enqueue(ready, seq, frame, node, drops)

    def advance_until(self, t_end: int) -> tuple[list[BusTx], list[BusDrop]]:
        txs, drops = [], []
        self._horizon = max(self._horizon, t_end)
        if not self.cfg.enabled:
            while self._arrivals and self._arrivals[0][0] < t_end:
                ready, _, frame, node = heapq.heappop(self._arrivals)
                txs.append(BusTx(frame, node, ready, ready, ready, frame_bits(frame.can_id, frame.data, self.cfg.stuffing)))
                self.stats.transmitted += 1
            return txs, drops
        while True:
            t = self.now_ns
            self._admit_until(t, drops)
            if t >= t_end and any(self._queues.values()):
                # The decision at t belongs to a later call: frames ready at <= t may still be
                # submitted, and must be admitted before anything expires at t.
                break
            for n in self._queues:
                self._expire(n, t, drops)
            heads = [(q[0][3].can_id, q[0][2], self._node_order[n], n) for n, q in self._queues.items() if q]
            if not heads:
                if not self._arrivals:
                    break
                nxt = self._arrivals[0][0]
                if nxt >= t_end:
                    self.now_ns = max(self.now_ns, min(nxt, t_end))
                    break
                self.now_ns = nxt                     # bus idle until next frame is ready
                continue
            if t >= t_end:
                break
            heads.sort()
            can_id, _, _, node = heads[0]
            if len(heads) > 1 and heads[1][0] == can_id:
                self.stats.same_id_contention += 1
            _, _, ready, frame = heapq.heappop(self._queues[node])
            bits = frame_bits(frame.can_id, frame.data, self.cfg.stuffing)
            dur = round(bits * self.cfg.bit_time_ns)
            txs.append(BusTx(frame, node, ready, t, t + dur, bits))
            self.now_ns = t + dur
            self.stats.transmitted += 1
            self.stats.busy_ns += dur
        return txs, drops

    def drain(self) -> tuple[list[BusTx], list[BusDrop]]:
        """Transmit everything still waiting (used after the last simulation step)."""
        return self.advance_until(2**62)

    def pending(self) -> int:
        return len(self._arrivals) + sum(len(q) for q in self._queues.values())


def utilization_bins(intervals: list[tuple[int, int]], t0: int, t1: int, bin_ns: int) -> list[float]:
    """Busy fraction per bin. Transmission intervals are split exactly at bin edges."""
    n = max(1, -(-(t1 - t0) // bin_ns))
    busy = [0] * n
    for s, e in intervals:
        s, e = max(s, t0), min(e, t1)
        while s < e:
            b = (s - t0) // bin_ns
            edge = t0 + (b + 1) * bin_ns
            seg_end = min(e, edge)
            busy[b] += seg_end - s
            s = seg_end
    return [busy[i] / min(bin_ns, t1 - (t0 + i * bin_ns)) for i in range(n)]
