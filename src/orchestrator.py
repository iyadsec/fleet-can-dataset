"""Experiment orchestrator (Milestone 1: benign fleet).

Pipeline per SUMO step k (time t_k on the SUMO timeline):
  SUMO --TraCI--> VehicleState(t_k) --CAN generator--> frames due in [t_k, t_k + step)
       --SocketCAN send (paced to SUMO time if realtime)--> vcanN --independent capture socket-->
       captured frame --recorder--> dataset row (timestamp = SUMO time of the frame)

Usage: python -m src.orchestrator --config config/experiment.yaml
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import can

from . import ground_truth, recorder
from .attack_engine import build_attacks
from .campaign_scheduler import build_schedule
from .can_bus import make_transport
from .can_generator import VehicleCanGenerator
from .config import ExperimentConfig, load_experiment
from .metadata import build_metadata, snapshot_configs, write_json
from .sumo_controller import SumoController
from . import validator


def check_channels(channels: list[str]) -> None:
    bad = []
    for ch in channels:
        state = Path(f"/sys/class/net/{ch}/operstate")
        flags = Path(f"/sys/class/net/{ch}/flags")
        if not state.exists() or not (int(flags.read_text(), 16) & 0x1):   # IFF_UP
            bad.append(ch)
    if bad:
        raise SystemExit(f"vCAN interfaces missing or down: {bad}. Run: sudo scripts/setup_vcan.sh {len(channels)}")


def _wait_until(target_perf_ns: int) -> None:
    while True:
        d = target_perf_ns - time.perf_counter_ns()
        if d <= 0:
            return
        if d > 2_000_000:
            time.sleep((d - 1_000_000) / 1e9)


def run(cfg: ExperimentConfig, session_id: str | None = None, validate: bool = True) -> tuple[str, str]:
    build_attacks(cfg.attacks)          # raises if attacks configured (not in Milestone 1)
    build_schedule(cfg.campaigns, cfg.seed)

    wall_start = datetime.now(timezone.utc)
    session_id = session_id or f"{cfg.experiment_id}_{wall_start:%Y%m%dT%H%M%SZ}"
    channels = [a.channel for a in cfg.fleet]
    check_channels(channels)

    gens = {a.vehicle_id: VehicleCanGenerator(a.vehicle_id, a.channel, cfg.profiles[a.profile]) for a in cfg.fleet}
    tx_log: dict[str, list[recorder.TxRecord]] = {ch: [] for ch in channels}
    send_errors = {ch: 0 for ch in channels}
    states_all = []

    transport, capture = make_transport(cfg.transport, channels)
    capture.start()
    time.sleep(0.2)                                  # listeners ready before first frame

    sumo = SumoController(str(cfg.sumo_config), cfg.step_length_s, cfg.seed)
    sumo.start()
    anchor_wall_ns, anchor_perf_ns = time.time_ns(), time.perf_counter_ns()   # SUMO t=0 <-> wall clock
    print(f"[{session_id}] SUMO {sumo.version}; running {cfg.duration_s:.1f} s simulated "
          f"({'real-time' if cfg.realtime else 'as fast as possible'}), vehicles -> {dict((a.vehicle_id, a.channel) for a in cfg.fleet)}")
    try:
        while sumo.sim_time_ns < cfg.duration_ns:
            states = sumo.step()
            states_all.extend(states)
            frames = []
            for s in states:
                if s.vehicle_id not in gens:
                    raise RuntimeError(f"SUMO vehicle {s.vehicle_id} has no fleet configuration")
                frames.extend(gens[s.vehicle_id].frames_for_step(s, cfg.step_ns))
            frames.sort(key=lambda fr: (fr.sim_time_ns, fr.vehicle_id, fr.can_id))
            for fr in frames:
                if cfg.realtime:
                    _wait_until(anchor_perf_ns + fr.sim_time_ns)
                try:
                    tx_ns = transport.send(fr.channel, fr.can_id, fr.data)
                except can.CanError:
                    send_errors[fr.channel] += 1      # counted and reported, never retried silently
                    continue
                tx_log[fr.channel].append(recorder.TxRecord(fr, tx_ns))
            if sumo.vehicles_remaining() == 0:
                break
        sim_end_ns = sumo.sim_time_ns
    finally:
        sumo.close()
        captures = capture.stop(cfg.capture_drain_s)
        transport.close()
    wall_end = datetime.now(timezone.utc)

    # --- record -----------------------------------------------------------------------------
    pairs, unexpected, stats = recorder.associate(tx_log, captures, send_errors)
    frame_rows, label_seed = recorder.build_frame_rows(pairs, session_id, cfg.scenario_id,
                                                       anchor_wall_ns if cfg.realtime else None)
    w = recorder.make_writer(cfg.output_format)
    root = cfg.output_root
    w.write(root / "raw" / session_id / "can_frames", recorder.FRAME_COLUMNS, frame_rows)
    w.write(root / "raw" / session_id / "vehicle_states", recorder.STATE_COLUMNS,
            recorder.build_state_rows(states_all, session_id))
    w.write(root / "raw" / session_id / "unmatched_captures",
            ["channel", "capture_time_ns", "can_id", "dlc", "payload", "seq"],
            [{"channel": c.channel, "capture_time_ns": c.capture_time_ns, "can_id": f"0x{c.can_id:03X}",
              "dlc": c.dlc, "payload": c.data.hex().upper(), "seq": c.seq} for c in unexpected])
    w.write(root / "ground_truth" / session_id / "frame_labels", ground_truth.LABEL_COLUMNS,
            ground_truth.frame_label_rows(label_seed))
    w.write(root / "ground_truth" / session_id / "campaigns", ground_truth.CAMPAIGN_COLUMNS,
            ground_truth.campaign_rows(cfg.campaigns, cfg.scenario_id, session_id))

    hashes = snapshot_configs(cfg, root / "metadata" / session_id / "config_snapshot")
    run_info = {
        "sumo_version": sumo.version, "sumo_command": sumo.cmd, "sim_start_ns": 0, "sim_end_ns": sim_end_ns,
        "steps": sumo.step_index, "anchor_wall_ns": anchor_wall_ns if cfg.realtime else None,
        "wall_start_utc": wall_start.isoformat(), "wall_end_utc": wall_end.isoformat(),
        "match_stats": {"per_channel": stats.per_channel, "total": stats.total},
        "model_stats": {"values_clamped_to_dbc_range": {v: g.clamped for v, g in gens.items()}},
    }
    write_json(build_metadata(cfg, session_id, run_info, hashes), root / "metadata" / session_id / "metadata.json")
    print(f"[{session_id}] transport: {stats.total}")

    overall = "SKIPPED"
    if validate:
        rep = validator.validate(session_id, root)
        validator.write_report(rep, session_id, validator.session_paths(root, session_id)["out_dir"])
        for c in rep.checks:
            print(f"  [{c['status']:4s}] {c['id']} {c['check']}: {c['detail']}")
        overall = rep.overall
        print(f"[{session_id}] VALIDATION: {overall}")
    return session_id, overall


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config/experiment.yaml")
    ap.add_argument("--duration", type=float, help="override simulation.duration_s")
    ap.add_argument("--no-validate", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_experiment(a.config)
    if a.duration:
        cfg.duration_s = a.duration
    _, overall = run(cfg, validate=not a.no_validate)
    return 0 if overall in ("PASS", "SKIPPED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
