"""Dataset recorder: turn captured vCAN frames into synchronized dataset rows.

A dataset row exists only for a frame that was actually captured on a vCAN interface. The ID,
DLC and payload in the row are the CAPTURED values. Each captured frame is associated with
the transmission record that produced it, which supplies its SUMO-timeline timestamp.

Association rule: on one vCAN channel with a single sender, the kernel delivers frames in send
order, so the k-th captured frame on channel c is the k-th frame sent on c. Every pair is
checked for identical CAN ID and payload; any disagreement, missing or extra frame is counted
and reported, never silently repaired.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from .can_bus import CapturedFrame
from .can_generator import TxFrame

FRAME_COLUMNS = [
    "frame_uid", "session_id", "scenario_id", "timestamp", "sim_time_ns",
    "capture_time_ns", "capture_offset_us", "vehicle_id", "channel",
    "can_id", "dlc", "payload",
]
STATE_COLUMNS = [
    "session_id", "timestamp", "sim_time_ns", "vehicle_id", "speed_mps", "accel_mps2",
    "x_m", "y_m", "angle_deg", "lane_id", "edge_id", "route_id",
]


@dataclass
class TxRecord:
    frame: TxFrame
    tx_wall_ns: int


@dataclass
class MatchStats:
    per_channel: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total(self) -> dict[str, int]:
        keys = ("sent", "send_errors", "captured", "matched", "content_mismatch", "missing", "unexpected")
        return {k: sum(c.get(k, 0) for c in self.per_channel.values()) for k in keys}


class TableWriter(ABC):
    @abstractmethod
    def write(self, path: Path, columns: list[str], rows: list[dict]) -> Path: ...


class CsvTableWriter(TableWriter):
    def write(self, path: Path, columns: list[str], rows: list[dict]) -> Path:
        path = path.with_suffix(".csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=columns, extrasaction="raise")
            w.writeheader()
            w.writerows(rows)
        return path


def make_writer(fmt: str) -> TableWriter:
    if fmt == "csv":
        return CsvTableWriter()
    if fmt == "parquet":
        raise NotImplementedError("parquet writer planned; add pyarrow and a ParquetTableWriter")
    raise ValueError(f"unknown output format {fmt}")


def fmt_time(ns: int) -> str:
    """SUMO-timeline seconds with full ns precision, e.g. 12.340000000."""
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}"


def associate(tx_log: dict[str, list[TxRecord]], captures: dict[str, list[CapturedFrame]],
              send_errors: dict[str, int]) -> tuple[list[tuple[TxRecord, CapturedFrame]], list[CapturedFrame], MatchStats]:
    pairs, unexpected, stats = [], [], MatchStats()
    for ch in sorted(set(tx_log) | set(captures)):
        sent, got = tx_log.get(ch, []), captures.get(ch, [])
        s = {"sent": len(sent), "send_errors": send_errors.get(ch, 0), "captured": len(got),
             "matched": 0, "content_mismatch": 0, "missing": 0, "unexpected": 0}
        for rec, cap in zip(sent, got):
            if rec.frame.can_id == cap.can_id and rec.frame.data == cap.data and not cap.is_extended:
                pairs.append((rec, cap))
                s["matched"] += 1
            else:
                s["content_mismatch"] += 1
                unexpected.append(cap)
        if len(got) > len(sent):
            s["unexpected"] = len(got) - len(sent)
            unexpected.extend(got[len(sent):])
        s["missing"] = max(len(sent) - len(got), 0)
        stats.per_channel[ch] = s
    return pairs, unexpected, stats


def build_frame_rows(pairs, session_id: str, scenario_id: str, anchor_wall_ns: int | None):
    """Return (frame_rows, label_rows). Sorted by SUMO time, then vehicle, then CAN ID."""
    pairs = sorted(pairs, key=lambda p: (p[0].frame.sim_time_ns, p[0].frame.vehicle_id, p[1].can_id))
    frames, labels = [], []
    for i, (rec, cap) in enumerate(pairs):
        f = rec.frame
        uid = f"{session_id}-F{i:09d}"
        offset = "" if anchor_wall_ns is None else f"{(cap.capture_time_ns - (anchor_wall_ns + f.sim_time_ns)) / 1e3:.1f}"
        frames.append({
            "frame_uid": uid, "session_id": session_id, "scenario_id": scenario_id,
            "timestamp": fmt_time(f.sim_time_ns), "sim_time_ns": f.sim_time_ns,
            "capture_time_ns": cap.capture_time_ns, "capture_offset_us": offset,
            "vehicle_id": f.vehicle_id, "channel": cap.channel,
            "can_id": f"0x{cap.can_id:03X}", "dlc": cap.dlc, "payload": cap.data.hex().upper(),
        })
        labels.append({"frame_uid": uid, "session_id": session_id, "frame_source": f.source,
                       "message_name": f.message})
    return frames, labels


def build_state_rows(states, session_id: str) -> list[dict]:
    return [{
        "session_id": session_id, "timestamp": fmt_time(s.sim_time_ns), "sim_time_ns": s.sim_time_ns,
        "vehicle_id": s.vehicle_id, "speed_mps": f"{s.speed_mps:.4f}", "accel_mps2": f"{s.accel_mps2:.4f}",
        "x_m": f"{s.x_m:.3f}", "y_m": f"{s.y_m:.3f}", "angle_deg": f"{s.angle_deg:.3f}",
        "lane_id": s.lane_id, "edge_id": s.edge_id, "route_id": s.route_id,
    } for s in states]
