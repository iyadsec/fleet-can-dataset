"""CAN transport abstraction.

The orchestrator only talks to `CanTransport` (send) and `CanCapture` (observe). The SocketCAN
implementation uses python-can on Linux vCAN interfaces. Real hardware, CANoe or trace replay
can be added later as other implementations without touching the scheduler, recorder or
ground-truth code.

What vCAN does: delivers frames between sockets on the same virtual interface via the Linux
kernel, with kernel receive timestamps. What vCAN does NOT do: bit timing, bitrate limits,
arbitration, error frames, ACK, electrical behaviour. Bus timing is therefore supplied by
src/bus_model.py before frames reach vCAN. See docs/timing_model.md.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

import can


@dataclass(frozen=True)
class CapturedFrame:
    """An observation: what a listener on the bus saw. Contains no ground truth."""
    channel: str
    capture_time_ns: int        # kernel receive timestamp (CLOCK_REALTIME), ns
    can_id: int
    is_extended: bool
    dlc: int
    data: bytes
    seq: int                    # receive order on this channel


class CanTransport(ABC):
    @abstractmethod
    def send(self, channel: str, can_id: int, data: bytes, node: str = "ecu") -> int:
        """Send one frame from a node ("ecu" or "attacker"). Return host wall time (ns) after the call."""

    @abstractmethod
    def close(self) -> None: ...


class CanCapture(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self, drain_s: float) -> dict[str, list[CapturedFrame]]:
        """Stop listening; return the captures not yet taken with take() (all captures if take() was never used)."""

    def take(self, channel: str) -> list[CapturedFrame]:
        """Remove and return the captures received so far on `channel`, in receive order (generator 1.3.0)."""
        raise NotImplementedError


class SocketCanTransport(CanTransport):
    """One sending socket per (channel, node): ECU traffic and attacker traffic leave the host
    from different sockets, like separate nodes on the bus. Bus timing comes from the logical
    bus model; frames are handed to vCAN already in modelled bus order."""

    def __init__(self, channels: list[str]):
        self.channels = channels
        self.buses: dict[tuple[str, str], can.BusABC] = {}
        self.errors: dict[str, int] = {ch: 0 for ch in channels}

    def _bus(self, channel: str, node: str):
        key = (channel, node)
        if key not in self.buses:
            self.buses[key] = can.Bus(interface="socketcan", channel=channel, receive_own_messages=False)
        return self.buses[key]

    def send(self, channel: str, can_id: int, data: bytes, node: str = "ecu") -> int:
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
        try:
            self._bus(channel, node).send(msg, timeout=0.1)
        except can.CanError:
            self.errors[channel] += 1
            raise
        return time.time_ns()

    def close(self) -> None:
        for b in self.buses.values():
            b.shutdown()


class SocketCanCapture(CanCapture):
    """One listener socket + thread per channel, independent of the sender sockets.

    Captures are queued per channel (thread-safe deque). The recorder takes them incrementally (take) so a
    session never has to hold every capture in memory (generator 1.3.0); `captured()` counts all receptions."""

    def __init__(self, channels: list[str]):
        self.channels = channels
        self.frames: dict[str, deque[CapturedFrame]] = {ch: deque() for ch in channels}
        self._count: dict[str, int] = {ch: 0 for ch in channels}
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._buses = {ch: can.Bus(interface="socketcan", channel=ch) for ch in channels}

    def _run(self, ch: str) -> None:
        bus, sink = self._buses[ch], self.frames[ch]
        while True:
            msg = bus.recv(timeout=0.05)
            if msg is None:
                if self._stop.is_set():
                    return
                continue
            sink.append(CapturedFrame(ch, round(msg.timestamp * 1e9), msg.arbitration_id,
                                      msg.is_extended_id, msg.dlc, bytes(msg.data), self._count[ch]))
            self._count[ch] += 1                   # only this thread writes this channel's counter

    def start(self) -> None:
        for ch in self.channels:
            t = threading.Thread(target=self._run, args=(ch,), name=f"capture-{ch}", daemon=True)
            t.start()
            self._threads.append(t)

    def captured(self, channel: str) -> int:
        return self._count[channel]

    def take(self, channel: str) -> list[CapturedFrame]:
        q, out = self.frames[channel], []
        while q:
            out.append(q.popleft())
        return out

    def stop(self, drain_s: float) -> dict[str, list[CapturedFrame]]:
        time.sleep(drain_s)
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        for b in self._buses.values():
            b.shutdown()
        return {ch: self.take(ch) for ch in self.channels}


def make_transport(kind: str, channels: list[str]) -> tuple[CanTransport, CanCapture]:
    if kind == "socketcan":
        return SocketCanTransport(channels), SocketCanCapture(channels)
    raise ValueError(f"unsupported CAN transport '{kind}'")
