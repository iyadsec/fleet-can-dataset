# Generation architecture (source dataset fleetcan-sim-scale-1.0)

fleetcan-sim-research-1.0 is a **derived release**: no frame was generated, regenerated or modified for it. This
page documents how the frozen source was produced (repository `iyadsec/fleet-can-dataset`, generator 1.3.0,
commits `f2128775` for 116 sessions and `7b659631` for 719 sessions; identical generation code, reproducibility
verified in M10).

```
 SUMO 1.28 (grid4x4 network, generated routes)          master clock = SUMO simulation time, 10 ms steps
        │  TraCI (Python): speed, acceleration, position per vehicle and step
        ▼
 Synthetic physical model + vehicle profile A/B/C       src/can_generator.py: signals -> DBC encoding (cantools),
        │  periodic messages (10/20/50 ms), ±1 % release jitter, ECU switch-on U[0,1] s
        ▼
 Attack engine (flooding / fuzzy / malfunction / replay) src/attack_engine.py; configured by campaign scheduler
        │  injected frames tagged with attack/campaign identity (ground truth only)
        ▼
 Logical CAN bus model, one bus per vehicle             src/bus_model.py: 500 kbit/s, exact bit stuffing, ID
        │  arbitration, per-node priority queues (32), 100 ms deadline; yields tx_start/tx_end per frame
        ▼
 SocketCAN vCAN transport (Linux kernel)                frames sent and captured on vcan<i>; every frame matched
        │  (content-checked) between send and capture; capture_time_ns = kernel receive time
        ▼
 Recorder + ground truth + validator                     src/stream_recorder.py, src/ground_truth.py, src/validator.py
           raw/<sid>/can_frames, ground_truth/<sid>/*, metadata/<sid>/*, validation/<sid>/*  (Parquet, zstd)
```

## Components

* **SUMO mobility.** SUMO provides vehicle kinematics only. **SUMO does not generate CAN traffic.** Fleets of
  N = 20/50/100 use generated routes (`grid_loops_v1`: seeded 30 m slots on three loops of the 4×4 grid); the G0
  bridge uses the same generator at N = 10 with the baseline first-K member rule.
* **Python/TraCI orchestration.** `src/orchestrator.py` steps SUMO in 10 ms steps (fast mode, not real-time paced),
  reads the vehicle state, drives the CAN generators, the attack engine and the bus model on the same timeline.
* **Synthetic CAN generation.** Three synthetic profiles (A/B/C, project DBC files, not OEM) encode the same physical
  state with different ID allocation, periods, phases, DLC and packing (`VEHICLE_PROFILES.md`).
* **Attack injection.** Four frozen families (`ATTACK_DEFINITIONS.md`) scheduled per vehicle by
  `src/campaign_scheduler.py` from the frozen matrix (`CAMPAIGN_DEFINITIONS.md`).
* **Logical CAN bus.** A timing/arbitration model, not a physical-layer simulation: no bit errors, error frames,
  retransmissions, bus-off, transceiver effects or gateways. One bus per vehicle.
* **SocketCAN/vCAN transport.** Real Linux virtual CAN interfaces carry every frame; the capture is matched frame by
  frame to the send (0 missing, 0 unexpected, 0 content mismatches in all sessions; `unmatched_captures` empty).
  vCAN has no bitrate; bus timing comes from the logical model.
* **Ground truth.** Written by the generator from the configuration and the injection records (never inferred from
  timing): frame labels, attacks, campaigns, membership, overlap, replay links, attack frames, dropped frames.
* **Validation.** The generator validator (V01–V…) passed for all 835 sessions; this release re-validates
  independently (`VALIDATION_METHODS.md`).

## Timelines

| Time | Meaning | Observable? |
|---|---|---|
| `sim_time_ns` | scheduled generation time (SUMO timeline) | no |
| `tx_start_ns` | start of transmission after arbitration/queueing (model) | no |
| `tx_end_ns` = release `timestamp_ns` | end of frame on the bus (model) — what a bus logger records | **yes** |
| `capture_time_ns` | host kernel receive time (wall clock) | no — correlated with generation order and host load |
| configured attack/campaign times | from the configuration | no — ground truth only |

## Derived release pipeline (this release)

`scripts/export_dataset.py` → per session (`research/session_export.py`): validate source → write detector-safe
observations (Parquet + gzip CSV, read-back verified) → bus timing metadata → ground truth (+ join keys) →
metadata → descriptors (vectorised original 24-D; equivalence verified against the reference implementation) →
window labels (separate) → derived observable timing → per-session statistics. Then `generate_statistics.py`
(aggregation, `research/aggregate.py`), `validate_dataset.py` (tests, compatibility, benign IF sanity check),
`generate_reports.py`, `verify_integrity.py` (manifest, SHA-256).
