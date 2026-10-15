# fleet-can-dataset

Research testbed that generates **synchronized, labelled, synthetic CAN traffic from a fleet of
simulated vehicles**, for PhD research on fleet-level detection of coordinated CAN attacks
(behavioural + temporal correlation). This repository does **data generation and ground truth
only**. It contains no detector.

> **Status: Milestone 1.** Benign pipeline only (scenario S0, 3 vehicles). Attacks and campaigns are
> deliberately not implemented yet.

## What is real and what is synthetic

| Layer | Source | Nature |
|---|---|---|
| Mobility (speed, acceleration, position, lane, route) | SUMO 1.28 via TraCI | Simulated (synthetic road network and routes) |
| CAN signals (RPM, gear, pedal, brake, steering) | `src/can_generator.py` | **Synthetic model** derived from SUMO state |
| CAN IDs, layouts, scaling | `dbc/generic_vehicle.dbc` | **Defined by this project.** Not OEM CAN IDs |
| CAN transport + capture | Linux SocketCAN **vCAN** | Real kernel frame delivery; **no** physical bus behaviour |
| Attacks / campaigns | *(later milestones)* | Injected / experimentally constructed |

The generated traffic is **not real vehicle CAN traffic**. SUMO does not simulate CAN.

## Architecture

```
SUMO --TraCI--> SumoController --VehicleState(t)--> VehicleCanGenerator (one per vehicle, DBC + profile)
     --TxFrame(sim_time)--> CanTransport (SocketCAN) --> vcanN --> CanCapture (separate socket per channel)
     --CapturedFrame--> recorder.associate (k-th sent == k-th captured, ID+payload checked)
     --> data/raw/<session>/can_frames.csv          (observations)
     --> data/ground_truth/<session>/*.csv         (labels, campaigns: separate files)
     --> data/metadata/<session>/metadata.json     (+ config snapshot with SHA-256)
     --> data/validation/<session>/validation_report.{json,md}
```
Details: [docs/architecture.md](docs/architecture.md), [docs/dataset_schema.md](docs/dataset_schema.md),
[docs/timing_model.md](docs/timing_model.md).

## Installation (Ubuntu 24.04, tested on arm64 UTM VM)

```bash
sudo apt-get install -y can-utils python3-venv git
git clone <this repo> ~/fleet-can-dataset && cd ~/fleet-can-dataset
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # includes SUMO 1.28.0 wheel
scripts/build_sumo_network.sh                                          # regenerate grid network (committed)
```
Exact versions used for each run are in `requirements.lock.txt` and in each run's `metadata.json`.

## vCAN setup (root, not persistent across reboot)

```bash
sudo scripts/setup_vcan.sh 3      # vcan0..vcan2 (idempotent)
scripts/test_vcan.sh 3            # cansend/candump + python-can send/receive per interface
sudo scripts/cleanup_vcan.sh all  # remove vcan<N> interfaces
```

## Run an experiment

```bash
scripts/run_experiment.sh                       # verify env, then run config/experiment.yaml (60 s, real time)
scripts/run_experiment.sh config/experiment.yaml --duration 10
.venv/bin/python -m src.validator --session <session_id>   # re-validate an existing session
.venv/bin/python -m pytest -q                              # unit + integration tests
```

## Configuration

* `config/experiment.yaml`: IDs, seed, SUMO step and duration, transport, output.
* `config/vehicles.yaml`: vehicle **profiles** (DBC, message periods/offsets, signal mapping, synthetic
  model parameters) and the fleet (vehicle → profile; channel `vcan{index}` by list order).
  Profiles are separate from code, so heterogeneous fleets need only new profiles/DBCs.
* `config/attacks.yaml`, `config/campaigns.yaml`: empty in Milestone 1.

## Reproducibility

Each session stores the seed, SUMO command and version, Python and package versions, kernel, git commit
(and dirty flag), SHA-256 and a copy of every config/DBC/SUMO input, and the time-base definition.
With `realtime: true`, SUMO-timeline content is deterministic for a given seed. Capture timestamps
reflect host execution and are not.

## Limitations (Milestone 1)

* vCAN has no bitrate, arbitration, bit timing, error frames or ACK. Bus-load effects are not emulated.
* One common synthetic profile; no sensor noise or period jitter yet (exact schedule on the SUMO timeline).
* Capture jitter depends on host/VM load; it is measured (`capture_offset_us`), never used as ground truth.
* vCAN interfaces must be recreated after reboot.
