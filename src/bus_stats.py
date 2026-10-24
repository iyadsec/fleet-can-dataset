"""Incremental bus statistics (generator 1.3.0, Milestone 8.1): produces exactly the bus_stats.json of
`orchestrator.bus_statistics` without keeping every transmission in memory.

Per channel: transmission count, busy time and 100 ms utilisation bins as exact integers (intervals are split at
bin edges exactly as bus_model.utilization_bins does), the latest transmission end, and the queue delays of
benign / attack transmissions in bus order. Queue delays are needed in full for the frozen mean / percentiles, so
they are appended to per-channel temporary int64 files and read back one channel at a time at the end.
Attack windows (known from the configuration) accumulate their busy time and benign transmissions while frames
are transmitted. Drops are kept by the caller (bounded by the attack load) and passed to finalize().
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SPILL_VALUES = 65_536


def _q(values) -> dict:
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return {"n": 0}
    return {"n": int(v.size), "mean": float(v.mean()), "p50": float(np.percentile(v, 50)),
            "p99": float(np.percentile(v, 99)), "max": float(v.max())}


class _Spill:
    """Append-only int64 sequence on disk with a small in-memory buffer."""

    def __init__(self, path: Path):
        self.path, self.buf, self.n = path, [], 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    def add(self, v: int) -> None:
        self.buf.append(v)
        if len(self.buf) >= SPILL_VALUES:
            self.flush()

    def flush(self) -> None:
        if self.buf:
            with open(self.path, "ab") as fh:
                np.asarray(self.buf, dtype=np.int64).tofile(fh)
            self.n += len(self.buf)
            self.buf = []

    def load(self) -> np.ndarray:
        self.flush()
        return np.fromfile(self.path, dtype=np.int64)

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)


class _Window:
    def __init__(self, lo: int, hi: int):
        self.lo, self.hi, self.busy, self.ben_qd = lo, hi, 0, []


class BusStatsAccumulator:
    def __init__(self, buses: dict, attacks, bin_ns: int, spill_dir: Path):
        self.buses, self.bin_ns = buses, bin_ns
        self.count = {ch: 0 for ch in buses}
        self.busy = {ch: 0 for ch in buses}
        self.max_end = {ch: None for ch in buses}
        self.bins: dict[str, dict[int, int]] = {ch: {} for ch in buses}
        safe = {ch: ch.replace("/", "_") for ch in buses}
        self.qd = {ch: (_Spill(spill_dir / f"{safe[ch]}.benign.i64"), _Spill(spill_dir / f"{safe[ch]}.attack.i64")) for ch in buses}
        self.windows: dict[str, list[_Window]] = {ch: [] for ch in buses}
        self.attack_windows = []                       # (attack, baseline window | None, during window)
        self.pending = []                              # generator 2.0.0: condition-triggered attacks not yet started
        for a in attacks:
            if getattr(a, "arm_ns", None) is not None:
                self.pending.append(a)
                continue
            s, e = a.start_ns, a.end_ns
            base_lo = max(0, s - (e - s))
            base = _Window(base_lo, s) if s > base_lo else None
            during = _Window(s, e)
            self.attack_windows.append((a, base, during))
            self.windows[a.channel] += [w for w in (base, during) if w is not None]

    def _start_pending(self) -> None:
        """A triggered attack gets its 'during' window once its trigger fired (frames of the triggering step are
        transmitted after the trigger is resolved, so none is missed). No baseline window: its position was not
        known in advance."""
        for a in [x for x in self.pending if x.trigger_ns is not None]:
            during = _Window(a.start_ns, a.end_ns)
            self.attack_windows.append((a, None, during))
            self.windows[a.channel].append(during)
            self.pending.remove(a)

    def add(self, ch: str, txs) -> None:
        if self.pending:
            self._start_pending()
        bins, B = self.bins[ch], self.bin_ns
        ben, att = self.qd[ch]
        wins = self.windows[ch]
        for t in txs:
            s, e = t.start_ns, t.end_ns
            self.count[ch] += 1
            self.busy[ch] += e - s
            if self.max_end[ch] is None or e > self.max_end[ch]:
                self.max_end[ch] = e
            x = s
            while x < e:                                   # split exactly at bin edges (bus_model.utilization_bins)
                b = x // B
                seg = min(e, (b + 1) * B)
                bins[b] = bins.get(b, 0) + seg - x
                x = seg
            qd = t.queue_delay_ns
            (att if t.frame.attack_id else ben).add(qd)
            for w in wins:
                w.busy += max(0, min(e, w.hi) - max(s, w.lo))
                if not t.frame.attack_id and w.lo <= t.ready_ns < w.hi:
                    w.ben_qd.append(qd)

    def finalize(self, sim_end_ns: int, all_drops: dict) -> dict:
        if self.pending:
            self._start_pending()
        out = {"bin_ns": self.bin_ns, "channels": {}, "attack_windows": []}
        for ch, bus in self.buses.items():
            last = max([sim_end_ns] + ([self.max_end[ch]] if self.max_end[ch] is not None else []))
            span_end = -(-last // self.bin_ns) * self.bin_ns
            n = max(1, -(-span_end // self.bin_ns))
            ben, att = (sp.load() for sp in self.qd[ch])
            busy = self.busy[ch]
            out["channels"][ch] = {
                "config": vars(bus.cfg), "transmitted": self.count[ch], "dropped": len(all_drops[ch]),
                "queue_drops": sum(d.kind == "queue_drop" for d in all_drops[ch]),
                "expired_drops": sum(d.kind == "expired_drop" for d in all_drops[ch]),
                "busy_ns": busy, "span_ns": span_end,
                "utilization_overall": busy / span_end if span_end else 0.0,
                "utilization_bins": [self.bins[ch].get(i, 0) / min(self.bin_ns, span_end - i * self.bin_ns) for i in range(n)],
                "queue_delay_us_benign": _q(ben / 1e3),
                "queue_delay_us_attack": _q(att / 1e3),
                "max_queue_len": bus.stats.max_queue_len, "same_id_contention": bus.stats.same_id_contention,
            }
            del ben, att
        for a, base, during in self.attack_windows:
            drops = all_drops[a.channel]

            def window(w: _Window):
                ben_drop = [d for d in drops if not d.frame.attack_id and w.lo <= d.ready_ns < w.hi]
                return {"start_ns": w.lo, "end_ns": w.hi, "utilization": w.busy / (w.hi - w.lo),
                        "benign_generated": len(w.ben_qd) + len(ben_drop), "benign_transmitted": len(w.ben_qd),
                        "benign_dropped": len(ben_drop),
                        "benign_queue_dropped": sum(d.kind == "queue_drop" for d in ben_drop),
                        "benign_expired_dropped": sum(d.kind == "expired_drop" for d in ben_drop),
                        "benign_queue_delay_us": _q([q / 1e3 for q in w.ben_qd])}

            out["attack_windows"].append({"attack_id": a.attack_id, "campaign_id": a.campaign_id, "channel": a.channel,
                                          "baseline": window(base) if base is not None else None, "during": window(during)})
        return out

    def cleanup(self) -> None:
        for ben, att in self.qd.values():
            ben.remove()
            att.remove()
