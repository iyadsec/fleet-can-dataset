"""Experiment orchestrator.

Pipeline per SUMO step k (window [t_k, t_k + step) on the SUMO timeline):
  SUMO --TraCI--> VehicleState(t_k) --CAN generator--> benign frames (generation times)
       --attack engine--> + injected / modified frames (attack ground truth attached)
       --logical CAN bus model (per vehicle)--> modelled transmission start/end, queueing, drops
       --SocketCAN send at modelled end time (paced to SUMO time if realtime)--> vcanN
       --independent capture socket--> captured frame --recorder--> dataset row

Usage: python -m src.orchestrator --config config/experiment.yaml [--duration S]
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import can
import numpy as np

from . import ground_truth, recorder, route_generator, validator
from .attack_engine import build_attacks, resolve_semantic_targets
from . import storage
from .bus_model import CanBusModel, utilization_bins
from .bus_stats import BusStatsAccumulator
from .chunked import DEFAULT_CHUNK_ROWS, ChunkedTable
from .stream_recorder import FrameStream
from .campaign_scheduler import attack_configs, build_schedule, check_scenario, describe
from .can_bus import make_transport
from .can_generator import VehicleCanGenerator
from .config import ExperimentConfig, derive_seed, load_experiment
from .metadata import build_metadata, snapshot_configs, write_json
from .sumo_controller import SumoController

UTIL_BIN_NS = 100_000_000          # 100 ms utilization bins


def check_channels(channels: list[str]) -> None:
    bad = []
    for ch in channels:
        flags = Path(f"/sys/class/net/{ch}/flags")
        if not flags.exists() or not (int(flags.read_text(), 16) & 0x1):   # IFF_UP
            bad.append(ch)
    if bad:
        raise SystemExit(f"vCAN interfaces missing or down: {bad}. Run: sudo scripts/setup_vcan.sh {len(channels)}")


def _wait_until(target_perf_ns: int) -> None:
    while True:
        d = target_perf_ns - time.perf_counter_ns()
        if d <= 0:
            return
        if d > 3_000_000:                         # coarse sleep, then spin: VM sleep overshoot is ~0.5-1 ms
            time.sleep((d - 2_000_000) / 1e9)


def _q(values) -> dict:
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return {"n": 0}
    return {"n": int(v.size), "mean": float(v.mean()), "p50": float(np.percentile(v, 50)),
            "p99": float(np.percentile(v, 99)), "max": float(v.max())}


def bus_statistics(cfg, buses, all_txs, all_drops, engine, sim_end_ns) -> dict:
    out = {"bin_ns": UTIL_BIN_NS, "channels": {}, "attack_windows": []}
    for ch, bus in buses.items():
        txs = all_txs[ch]
        last = max([sim_end_ns] + [t.end_ns for t in txs])
        span_end = -(-last // UTIL_BIN_NS) * UTIL_BIN_NS      # whole bins only (no partial last bin)
        intervals = [(t.start_ns, t.end_ns) for t in txs]
        out["channels"][ch] = {
            "config": vars(bus.cfg), "transmitted": len(txs), "dropped": len(all_drops[ch]),
            "queue_drops": sum(d.kind == "queue_drop" for d in all_drops[ch]),
            "expired_drops": sum(d.kind == "expired_drop" for d in all_drops[ch]),
            "busy_ns": sum(e - s for s, e in intervals), "span_ns": span_end,
            "utilization_overall": sum(e - s for s, e in intervals) / span_end if span_end else 0.0,
            "utilization_bins": utilization_bins(intervals, 0, span_end, UTIL_BIN_NS),
            "queue_delay_us_benign": _q([t.queue_delay_ns / 1e3 for t in txs if not t.frame.attack_id]),
            "queue_delay_us_attack": _q([t.queue_delay_ns / 1e3 for t in txs if t.frame.attack_id]),
            "max_queue_len": bus.stats.max_queue_len, "same_id_contention": bus.stats.same_id_contention,
        }
    for a in engine.attacks:
        ch, s, e = a.channel, a.start_ns, a.end_ns
        txs, drops = all_txs[ch], all_drops[ch]

        def window(lo, hi):
            iv = [(t.start_ns, t.end_ns) for t in txs]
            busy = sum(max(0, min(t1, hi) - max(t0, lo)) for t0, t1 in iv)
            ben_tx = [t for t in txs if not t.frame.attack_id and lo <= t.ready_ns < hi]
            ben_drop = [d for d in drops if not d.frame.attack_id and lo <= d.ready_ns < hi]
            return {"start_ns": lo, "end_ns": hi, "utilization": busy / (hi - lo),
                    "benign_generated": len(ben_tx) + len(ben_drop), "benign_transmitted": len(ben_tx),
                    "benign_dropped": len(ben_drop),
                    "benign_queue_dropped": sum(d.kind == "queue_drop" for d in ben_drop),
                    "benign_expired_dropped": sum(d.kind == "expired_drop" for d in ben_drop),
                    "benign_queue_delay_us": _q([t.queue_delay_ns / 1e3 for t in ben_tx])}

        base_lo = max(0, s - (e - s))
        out["attack_windows"].append({"attack_id": a.attack_id, "campaign_id": a.campaign_id, "channel": ch,
                                      "baseline": window(base_lo, s) if s > base_lo else None,
                                      "during": window(s, e)})
    return out


SESSION_DIRS = ("raw", "ground_truth", "validation", "metadata")     # metadata last: its presence = complete session


def run(cfg: ExperimentConfig, session_id: str | None = None, validate: bool = True,
        chunk_rows: int = DEFAULT_CHUNK_ROWS) -> tuple[str, str]:
    """Generate one session with bounded memory (generator 1.3.0).

    Frames are recorded as they are captured (src/stream_recorder.py) through chunked writers
    (src/chunked.py); the session is assembled under <output>/.incomplete/<session>/, validated there and only
    then moved into <output>/{raw,ground_truth,validation,metadata}/<session> (metadata last). An interrupted run
    never leaves an apparently complete session; an existing session is never overwritten."""
    profiles_by_vehicle = {a.vehicle_id: cfg.profiles[a.profile] for a in cfg.fleet}
    campaigns = build_schedule(cfg.campaigns, cfg.seed, [a.vehicle_id for a in cfg.fleet])
    problems = check_scenario(cfg.scenario_id, cfg.attacks, campaigns)
    if problems:
        raise SystemExit(f"scenario {cfg.scenario_id} inconsistent with configuration: {problems}")
    all_attacks = resolve_semantic_targets(list(cfg.attacks) + attack_configs(campaigns), profiles_by_vehicle)
    engine = build_attacks(all_attacks, cfg.fleet, profiles_by_vehicle, cfg.seed,
                           campaign_ids={c.campaign_id for c in campaigns})

    wall_start = datetime.now(timezone.utc)
    session_id = session_id or f"{cfg.experiment_id}_{wall_start:%Y%m%dT%H%M%SZ}"
    root = cfg.output_root
    existing = [str(root / d / session_id) for d in SESSION_DIRS if (root / d / session_id).exists()]
    if existing:
        raise SystemExit(f"session {session_id} already exists ({existing}); refusing to overwrite")
    stage = root / ".incomplete" / session_id                  # never a valid session location
    if stage.exists():
        shutil.rmtree(stage)                                   # left over from an interrupted run of this id
    stage.mkdir(parents=True)
    route_info = None
    if cfg.route_generation:                          # generator 1.2.0: generated scenario (new path only)
        scen, route_info = route_generator.write_scenario(
            root / "generated_sumo" / session_id, Path(cfg.route_generation["network"]),
            [a.vehicle_id for a in cfg.fleet], derive_seed(cfg.seed, "route_generation"), cfg.duration_s)
        cfg = replace(cfg, sumo_config=scen)
    channels = [a.channel for a in cfg.fleet]
    check_channels(channels)

    jit = cfg.fleet_info.get("ecu_jitter") or {}
    model_seed = derive_seed(cfg.seed, "vehicle_model")           # generator 2.0.0 (unused by profiles A-C)
    gens = {a.vehicle_id: VehicleCanGenerator(a.vehicle_id, a.channel, cfg.profiles[a.profile], a.switch_on_ns,
                                              float(jit.get("fraction", 0.0)), int(jit.get("seed") or 0), model_seed)
            for a in cfg.fleet}
    buses = {a.channel: CanBusModel(replace(cfg.profiles[a.profile].bus, enabled=cfg.bus_model == "logical"), a.channel)
             for a in cfg.fleet}
    all_drops = {ch: [] for ch in channels}          # bounded by the attack load (frames that never reach the bus)
    send_errors = {ch: 0 for ch in channels}
    raw, gt, md = stage / "raw" / session_id, stage / "ground_truth" / session_id, stage / "metadata" / session_id
    fmt = cfg.output_format
    tables = {"frames": ChunkedTable(raw / "can_frames", recorder.FRAME_COLUMNS, fmt, chunk_rows),
              "states": ChunkedTable(raw / "vehicle_states", recorder.STATE_COLUMNS, fmt, chunk_rows),
              "labels": ChunkedTable(gt / "frame_labels", ground_truth.LABEL_COLUMNS, fmt, chunk_rows),
              "attack_frames": ChunkedTable(gt / "attack_frames", ground_truth.ATTACK_FRAME_COLUMNS, fmt, chunk_rows),
              "replay": ChunkedTable(gt / "replay_map", ground_truth.REPLAY_COLUMNS, fmt, chunk_rows)}
    bstat = BusStatsAccumulator(buses, engine.attacks, UTIL_BIN_NS, stage / "tmp" / "queue_delays")

    transport, capture = make_transport(cfg.transport, channels)
    capture.start()
    time.sleep(0.2)                                  # listeners ready before first frame

    def send(txs):
        for tx in txs:
            if cfg.realtime:
                _wait_until(anchor_perf_ns + tx.end_ns)
            try:
                tx_ns = transport.send(tx.frame.channel, tx.frame.can_id, tx.frame.data,
                                       node="attacker" if tx.node == "ATTACKER" else "ecu")
            except can.CanError:
                send_errors[tx.frame.channel] += 1    # counted and reported, never retried silently
                continue
            stream.on_sent(recorder.TxRecord(tx, tx_ns))

    def advance(t_end):
        txs = []
        for ch, bus in buses.items():
            t, d = bus.advance_until(t_end) if t_end is not None else bus.drain()
            bstat.add(ch, t)
            all_drops[ch].extend(d)
            txs.extend(t)
        engine.observe(txs)
        return txs

    sumo = SumoController(str(cfg.sumo_config), cfg.step_length_s, cfg.seed)
    sumo.start()
    anchor_wall_ns, anchor_perf_ns = time.time_ns(), time.perf_counter_ns()   # SUMO t=0 <-> wall clock
    stream = FrameStream(session_id, cfg.scenario_id, channels, anchor_wall_ns if cfg.realtime else None, tables, engine.ctx)
    print(f"[{session_id}] SUMO {sumo.version}; {cfg.duration_s:.1f} s simulated "
          f"({'real-time' if cfg.realtime else 'as fast as possible'}), bus model={cfg.bus_model}, "
          f"campaigns={[c.campaign_id for c in campaigns]}, attacks={[a.attack_id for a in engine.attacks]}, vehicles -> {dict((a.vehicle_id, a.channel) for a in cfg.fleet)}")
    flow_timeouts = 0

    def wait_for_capture(timeout_s: float = 5.0) -> None:
        """Fast mode only: let every capture socket catch up before sending more. Logical results
        never depend on this; it only prevents kernel receive-buffer overflow (lost captures)."""
        nonlocal flow_timeouts
        deadline = time.perf_counter() + timeout_s
        while any(capture.captured(ch) < stream.counts[ch]["sent"] for ch in channels):
            if time.perf_counter() > deadline:
                flow_timeouts += 1
                return
            time.sleep(0.0002)

    held = []                                        # modelled frames not yet due for sending
    try:
        while sumo.sim_time_ns < cfg.duration_ns:
            states = sumo.step()
            for s in states:
                tables["states"].add(recorder.state_row(s, session_id))
            t0, t1 = sumo.sim_time_ns, sumo.sim_time_ns + cfg.step_ns
            frames = []
            for s in states:
                if s.vehicle_id not in gens:
                    raise RuntimeError(f"SUMO vehicle {s.vehicle_id} has no fleet configuration")
                frames.extend(gens[s.vehicle_id].frames_for_step(s, cfg.step_ns))
            frames = engine.process(frames, t0, t1, {s.vehicle_id for s in states}, {s.vehicle_id: s for s in states})
            for fr in frames:
                buses[fr.channel].submit(fr, fr.node)
            held.extend(advance(t1))
            # Send, in modelled time order, every frame that ends before t1. Later frames wait so
            # frames of the next step on another bus can still be interleaved correctly.
            held.sort(key=lambda x: (x.end_ns, x.frame.vehicle_id))
            due = [x for x in held if x.end_ns < t1]
            held = held[len(due):]
            send(due)
            if not cfg.realtime:
                wait_for_capture()
            stream.pump(capture)
            if sumo.vehicles_remaining() == 0:
                break
        sim_end_ns = sumo.sim_time_ns
        held.extend(advance(None))                   # drain queues: frames generated before the end
        held.sort(key=lambda x: (x.end_ns, x.frame.vehicle_id))
        send(held)
    except BaseException:
        for t in tables.values():
            t.abort()
        raise
    finally:
        sumo.close()
        remaining = capture.stop(cfg.capture_drain_s)
        transport.close()
    wall_end = datetime.now(timezone.utc)

    # --- finish recording (bounded: rows were written while the run progressed) ----------------
    stats = stream.finish(remaining, send_errors)
    drops = [d for ch in channels for d in all_drops[ch]]
    for t in tables.values():
        t.finalize()
    unexpected = stream.unexpected_captures()
    storage.write_table(raw / "unmatched_captures", ["channel", "capture_time_ns", "can_id", "dlc", "payload", "seq"],
                        [{"channel": c.channel, "capture_time_ns": c.capture_time_ns, "can_id": f"0x{c.can_id:03X}",
                          "dlc": c.dlc, "payload": c.data.hex().upper(), "seq": c.seq} for c in unexpected], fmt)
    storage.write_table(gt / "dropped_frames", ground_truth.DROP_COLUMNS, ground_truth.dropped_rows(drops, session_id), fmt)
    storage.write_table(gt / "attacks", ground_truth.ATTACK_COLUMNS,
                        ground_truth.attack_rows_from_tally(engine, stream.tally, drops, cfg.scenario_id, session_id), fmt)
    by_attack = {a.attack_id: a for a in engine.attacks}
    unfired = [a.attack_id for a in engine.attacks if a.arm_ns is not None and a.trigger_ns is None]
    if unfired:
        raise SystemExit(f"condition-triggered attacks never started (session too short for their timeout): {unfired}")
    for c in campaigns:                      # generator 2.0.0: triggered members start when their trigger fired
        for m in c.members:
            a = by_attack[m.attack_id]
            if a.arm_ns is not None:
                m.start_ns, m.end_ns = a.start_ns, a.end_ns
                m.relative_delay_ns = a.start_ns - c.start_ns
    c_rows, m_rows, o_rows = ground_truth.campaign_rows(campaigns, cfg.scenario_id, session_id)
    storage.write_table(gt / "campaign_controller", ground_truth.CONTROLLER_COLUMNS,
                        ground_truth.controller_rows(campaigns, engine, session_id), fmt)
    storage.write_table(gt / "campaigns", ground_truth.CAMPAIGN_COLUMNS, c_rows, fmt)
    storage.write_table(gt / "campaign_membership", ground_truth.MEMBERSHIP_COLUMNS, m_rows, fmt)
    storage.write_table(gt / "campaign_overlap", ground_truth.OVERLAP_COLUMNS, o_rows, fmt)

    bstats = bstat.finalize(sim_end_ns, all_drops)
    bstat.cleanup()
    write_json(bstats, md / "bus_stats.json")
    hashes = snapshot_configs(cfg, md / "config_snapshot")
    run_info = {
        "sumo_version": sumo.version, "sumo_command": sumo.cmd, "sim_start_ns": 0, "sim_end_ns": sim_end_ns,
        "steps": sumo.step_index, "anchor_wall_ns": anchor_wall_ns if cfg.realtime else None,
        "wall_start_utc": wall_start.isoformat(), "wall_end_utc": wall_end.isoformat(),
        "match_stats": {"per_channel": stats.per_channel, "total": stats.total},
        "bus_totals": {ch: {"generated": bstat.count[ch] + len(all_drops[ch]), "transmitted": bstat.count[ch],
                            "dropped": len(all_drops[ch]),
                            "queue_dropped": sum(d.kind == "queue_drop" for d in all_drops[ch]),
                            "expired_dropped": sum(d.kind == "expired_drop" for d in all_drops[ch])}
                       for ch in channels},
        "attack_configuration": all_attacks,
        "campaign_schedule": describe(campaigns),
        "model_stats": {"values_clamped_to_dbc_range": {v: g.clamped for v, g in gens.items()}},
        "execution": {"mode": "realtime" if cfg.realtime else "fast", "flow_control_timeouts": flow_timeouts,
                      "wall_seconds": round((wall_end - wall_start).total_seconds(), 3),
                      "recorder": {"streaming": True, "chunk_rows": chunk_rows,
                                   "max_frames_awaiting_capture": stream.max_waiting}},
        "attacks": engine.describe(),
        "route_generation": route_info,
    }
    write_json(build_metadata(cfg, session_id, run_info, hashes), md / "metadata.json")
    shutil.rmtree(stage / "tmp", ignore_errors=True)
    print(f"[{session_id}] transport: {stats.total}; bus dropped: {len(drops)}; "
          + ", ".join(f"{ch} util={v['utilization_overall']:.3f}" for ch, v in bstats["channels"].items()))

    overall = "SKIPPED"
    if validate:
        rep = validator.validate(session_id, stage)
        validator.write_report(rep, session_id, validator.session_paths(stage, session_id)["out_dir"])
        for c in rep.checks:
            print(f"  [{c['status']:4s}] {c['id']} {c['check']}: {c['detail']}")
        overall = rep.overall
        print(f"[{session_id}] VALIDATION: {overall}")
    # --- publish: move the finished session into place, metadata last --------------------------
    for d in SESSION_DIRS:
        src = stage / d / session_id
        if src.exists():
            (root / d).mkdir(parents=True, exist_ok=True)
            os.replace(src, root / d / session_id)
    shutil.rmtree(stage, ignore_errors=True)
    return session_id, overall


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config/experiment.yaml")
    ap.add_argument("--duration", type=float, help="override simulation.duration_s (recorded in metadata)")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--fast", action="store_true", help="faster than real time (overrides simulation.realtime)")
    ap.add_argument("--realtime", action="store_true", help="force real-time pacing (overrides simulation.realtime)")
    ap.add_argument("--storage", choices=["csv", "parquet"], help="override storage.format")
    ap.add_argument("--session-id", help="explicit session id (default: <experiment_id>_<UTC start>)")
    ap.add_argument("--chunk-rows", type=int, default=DEFAULT_CHUNK_ROWS,
                    help="rows buffered per table before a chunk is written (implementation detail, not semantics)")
    a = ap.parse_args(argv)
    cfg = load_experiment(a.config)
    if a.duration:
        cfg.duration_s = a.duration
    if a.fast and a.realtime:
        ap.error("--fast and --realtime are mutually exclusive")
    if a.fast:
        cfg.realtime = False
    if a.realtime:
        cfg.realtime = True
    if a.storage:
        cfg.output_format = a.storage
    _, overall = run(cfg, session_id=a.session_id, validate=not a.no_validate, chunk_rows=a.chunk_rows)
    return 0 if overall in ("PASS", "SKIPPED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
