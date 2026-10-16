"""Streaming recorder (generator 1.3.0, Milestone 8.1): bounded-memory replacement of the end-of-run
`recorder.associate` + `build_frame_rows` + ground-truth row building. Same rules, applied incrementally:

Pairing (unchanged): on every channel the k-th captured frame belongs to the k-th frame sent to that channel.
  A pair with identical CAN ID + payload (standard frame) is matched; otherwise the capture is `unexpected`
  and the sent frame gets no row. Captures beyond the sent frames are `unexpected`, sent frames never
  captured are `missing`. Captures that arrive before their send record are kept until it exists, so the
  positional rule is exactly the batch rule.
Order and identity (unchanged): rows are emitted in global send order, which is the frozen sort key
  (tx_end_ns, vehicle_id) — asserted for every emitted row. The frame UID counter advances only for matched
  pairs, so frame_uid = <session>-F<rank of the matched frame in that order>, as before.
Memory: a sent record waits only until its own capture is paired (fast mode: < 1 step); every row is handed
  to a ChunkedTable immediately.
Replay: frame UIDs are kept only for frames registered as Replay sources (AttackContext.replay_source_refs)
  and released when the last replay frame referring to them is written.
"""

from __future__ import annotations

from collections import deque

from .ground_truth import AttackTally, attack_frame_row, label_row, replay_row
from .recorder import MatchStats, TxRecord, frame_row, frame_uid

_PENDING, _OK, _BAD, _MISSING = 0, 1, 2, 3


class FrameStream:
    def __init__(self, session_id: str, scenario_id: str, channels: list[str], anchor_wall_ns: int | None,
                 tables: dict, attack_ctx):
        self.sid, self.scenario, self.anchor = session_id, scenario_id, anchor_wall_ns
        self.channels = list(channels)
        self.t = tables                                   # name -> ChunkedTable (frames, labels, attack_frames, replay)
        self.ctx = attack_ctx
        self.sent_q: dict[str, deque] = {ch: deque() for ch in channels}
        self.cap_q: dict[str, deque] = {ch: deque() for ch in channels}
        self.order: deque = deque()                       # entries [record, status, capture] in send order
        self.unexpected: dict[str, list] = {ch: [] for ch in channels}
        self.counts = {ch: {"sent": 0, "captured": 0, "matched": 0, "content_mismatch": 0} for ch in channels}
        self.next_uid = 0
        self.last_key = None
        self.tally = AttackTally()
        self.uid_of_source: dict[int, str] = {}
        self.max_waiting = 0

    # ------------------------------------------------------------------ inputs
    def on_sent(self, rec: TxRecord) -> None:
        ch = rec.frame.channel
        entry = [rec, _PENDING, None]
        self.counts[ch]["sent"] += 1
        self.sent_q[ch].append(entry)
        self.order.append(entry)
        self._pair(ch)

    def on_captures(self, ch: str, caps) -> None:
        if caps:
            self.counts[ch]["captured"] += len(caps)
            self.cap_q[ch].extend(caps)
            self._pair(ch)

    def _pair(self, ch: str) -> None:
        sq, cq = self.sent_q[ch], self.cap_q[ch]
        while sq and cq:
            entry, cap = sq.popleft(), cq.popleft()
            f = entry[0].frame
            if f.can_id == cap.can_id and f.data == cap.data and not cap.is_extended:
                entry[1], entry[2] = _OK, cap
                self.counts[ch]["matched"] += 1
            else:
                entry[1] = _BAD
                self.counts[ch]["content_mismatch"] += 1
                self.unexpected[ch].append(cap)

    def pump(self, capture) -> None:
        for ch in self.channels:
            self.on_captures(ch, capture.take(ch))
        self._emit()

    # ------------------------------------------------------------------ output
    def _emit(self) -> None:
        if len(self.order) > self.max_waiting:
            self.max_waiting = len(self.order)
        while self.order and self.order[0][1] != _PENDING:
            rec, status, cap = self.order.popleft()
            if status == _OK:
                self._write(rec, cap)

    def _write(self, rec: TxRecord, cap) -> None:
        f = rec.frame
        key = (rec.tx.end_ns, f.vehicle_id)
        if self.last_key is not None and not key > self.last_key:
            raise RuntimeError(f"stream order violated: {key} after {self.last_key}")
        self.last_key = key
        uid = frame_uid(self.sid, self.next_uid)
        self.next_uid += 1
        self.t["frames"].add(frame_row(rec, cap, uid, self.sid, self.scenario, self.anchor))
        self.t["labels"].add(label_row(uid, f, self.sid))
        self.tally.add(f)
        fid = id(f)
        if fid in self.ctx.replay_source_refs:
            self.uid_of_source[fid] = uid
        if f.attack_id:
            self.t["attack_frames"].add(attack_frame_row(uid, f))
            if f.attack_type == "replay":
                oid = id(f.ref["original_frame"])
                self.t["replay"].add(replay_row(uid, f, rec.tx.end_ns, self.uid_of_source.get(oid, "")))
                left = self.ctx.replay_source_refs.get(oid, 0) - 1
                if left <= 0:
                    self.ctx.replay_source_refs.pop(oid, None)
                    self.uid_of_source.pop(oid, None)
                else:
                    self.ctx.replay_source_refs[oid] = left

    def finish(self, remaining_captures: dict, send_errors: dict) -> MatchStats:
        """After capture.stop(): pair the rest, mark unpaired sends missing and unpaired captures unexpected."""
        for ch in self.channels:
            self.on_captures(ch, remaining_captures.get(ch, []))
        stats = MatchStats()
        for ch in sorted(self.channels):
            for entry in self.sent_q[ch]:
                entry[1] = _MISSING
            missing, extra = len(self.sent_q[ch]), len(self.cap_q[ch])
            self.unexpected[ch].extend(self.cap_q[ch])
            self.sent_q[ch].clear()
            self.cap_q[ch].clear()
            c = self.counts[ch]
            stats.per_channel[ch] = {"sent": c["sent"], "send_errors": send_errors.get(ch, 0), "captured": c["captured"],
                                     "matched": c["matched"], "content_mismatch": c["content_mismatch"],
                                     "missing": missing, "unexpected": extra}
        self._emit()
        assert not self.order
        return stats

    def unexpected_captures(self) -> list:
        return [cap for ch in sorted(self.channels) for cap in self.unexpected[ch]]
