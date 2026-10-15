# Architecture

| Module | Responsibility | Knows about labels? |
|---|---|---|
| `src/config.py` | Load/check YAML; vehicle → profile → channel mapping (`vcan{index}`, any fleet size) | no |
| `src/sumo_controller.py` | Start SUMO, step it, read subscribed vehicle state; integer-ns master clock | no |
| `src/can_generator.py` | Synthetic vehicle model + per-message ECU tasks; DBC encoding via cantools | no |
| `src/attack_engine.py` | Flooding, Fuzzy, Malfunction (inject/replace), Replay from config; tags frames with attack_id | creates attack provenance |
| `src/bus_model.py` | Logical CAN bus per vehicle: bit-exact duration, ID arbitration, node queues, drops, utilization | no |
| `src/can_bus.py` | `CanTransport` / `CanCapture` interfaces; SocketCAN implementation (ECU + attacker sockets) | no |
| `src/recorder.py` | Associate captured frames with transmissions; write observation tables | no |
| `src/ground_truth.py` | Frame labels from provenance; campaign table from config | yes (only module) |
| `src/campaign_scheduler.py` | *(Milestone 3+)* simultaneous / staggered / random / overlap / independent | n/a |
| `src/metadata.py` | Versions, seeds, hashes, config snapshot | no |
| `src/validator.py` | Automatic checks → JSON + Markdown report | reads both, checks consistency |
| `src/orchestrator.py` | Runs the pipeline for one session | no |

## Extensibility

* **Transport:** implement `CanTransport` + `CanCapture` (e.g. physical CAN adapter via python-can,
  CANoe, trace replay) and register it in `make_transport`. The scheduler, recorder, ground truth and
  configuration are unchanged.
* **Vehicle heterogeneity:** add a profile (and DBC) to `config/vehicles.yaml`. The generator reads
  CAN IDs, periods, offsets and signal mappings from the profile; nothing assumes a common layout.
* **Real CAN traces:** a trace-replay generator would emit `TxFrame`s with `source` set accordingly.
* **Parquet:** add a `ParquetTableWriter` to `make_writer`.

## Separation of observations and ground truth

`can_frames.csv` holds only what a listener on the bus (plus the shared SUMO clock) provides.
`frame_labels.csv`, `attacks.csv`, `attack_frames.csv`, `replay_map.csv`, `dropped_frames.csv` and
`campaigns.csv` live under `data/ground_truth/` and join on `frame_uid` / `attack_id`.
Benign generation never receives attack information; the attack engine sees benign frames only
the way an attacker on the bus would (latest generated frame, transmitted frames).
Labels come from the provenance of each frame (the component that created it) and from campaign
configuration. They never come from timing or detector output.
