# fleet-can-dataset

Testbed and dataset for PhD research on **fleet-level detection of coordinated CAN attacks** (FLEET-GUARD). It
generates a **synthetic CAN dataset with the SUMO + synthetic CAN testbed**: synchronized CAN traffic from a fleet of
simulated vehicles, with attacks and coordinated campaigns, and with the ground truth stored separately. It is not
real vehicle CAN data.

This repository covers data generation and ground truth. The FLEET-GUARD detector is not part of it. The only
detection code here is the earlier M7 local Isolation Forest baseline in `detection/`.

## The dataset: one family, two frozen components

The FleetCAN-Sim dataset is a single dataset family, generated in two stages. Each stage was frozen as a separate,
immutable component:

| Component | Fleet sizes | Sessions | Generated | Generator |
|---|---|---|---|---|
| **fleetcan-sim-1.1** (small fleets) | N = 2, 5, 10 | 610 (122 configurations × 5) | Milestone 6 | 1.1.0, fixed SUMO routes |
| **fleetcan-sim-scale-1.0** (large fleets) | N = 20, 50, 100, plus an N = 10 route-source bridge | 835 (167 configurations × 5) | Milestone 10 | 1.3.0, generated SUMO routes |

Together they cover N = 2 … 100 with the same four attack families, scenario types (S0–S3, mixed), frozen timing
anchors and ground-truth schema. The large-fleet component adds bounded-spread timing for large campaigns and
timing-matched independent hard negatives. The N = 10 bridge sessions in the large-fleet component measure the effect of switching from
fixed to generated routes.

**Content of every session, in both components:**
* raw observable CAN data (`raw/`);
* separate ground truth (`ground_truth/`);
* metadata and provenance (`metadata/`);
* the generator validation report (`validation/`).

**Descriptors are computed by the FLEET-GUARD pipeline from `raw/`, for both components.** The dataset itself is the
raw CAN data plus ground truth.

**Legacy note.** fleetcan-sim-1.1 also contains 24-D descriptor files (`analysis/<session>/`). They are a legacy
artifact of the Milestone 6 protocol, which still included this repository's own descriptor layer, and they were the
input of the M7 Isolation Forest baseline. They stay only because the component is frozen and immutable. **They are
not FLEET-GUARD input:** FLEET-GUARD computes its descriptors from `raw/` so that every fleet size is processed the
same way. fleetcan-sim-scale-1.0 contains no descriptors.

**Detector-safe use.** Build detector inputs only from observable CAN fields, using an explicit allow-list.
`raw/*/can_frames` contains `scenario_id` for compatibility and provenance; it must not be used as a feature. All of
`ground_truth/` and the provenance metadata are for evaluation only.

| | fleetcan-sim-1.1 | fleetcan-sim-scale-1.0 |
|---|---|---|
| Generation report | `docs/final_protocol.md`, `docs/PROCEDURE.md` §5 | `docs/m10_expanded_dataset_generation.md` |
| Freeze records (in Git) | `results/datasets/fleetcan-sim-1.1/` | `results/datasets/fleetcan-sim-scale-1.0/` |
| Download | not published yet (on the VM, verifiable against the committed `SHA256SUMS`) | GitHub release `dataset-fleetcan-sim-scale-1.0`, see [docs/dataset_download.md](docs/dataset_download.md) |

[docs/PROCEDURE.md](docs/PROCEDURE.md) describes the full procedure: environment, component map, milestone history,
reproduction commands and how to verify a copy of the data. The dataset files are not in Git (`data/` is
git-ignored); Git holds the code, the generation specifications, the freeze records and the complete hash lists.

## What is real and what is synthetic

| Layer | Source | Nature |
|---|---|---|
| Mobility (speed, acceleration, position, lane, route) | SUMO 1.28 via TraCI | Simulated (synthetic road network and routes) |
| CAN signals (RPM, gear, pedal, brake, steering) | `src/can_generator.py` | **Synthetic model** derived from SUMO state |
| CAN IDs, layouts, scaling | `dbc/generic_vehicle.dbc` | **Defined by this project.** Not OEM CAN IDs |
| Bus timing (duration, arbitration, queueing, drops) | `src/bus_model.py` | **Logical model** of Classical CAN at 500 kbit/s, not a physical-layer simulation |
| CAN transport + capture | Linux SocketCAN **vCAN** | Real kernel frame delivery; **no** bus behaviour of its own |
| Attacks | `src/attack_engine.py` + `config/m2/` | **Injected** from configuration, single vehicle |
| Campaigns | `src/campaign_scheduler.py` + `config/m3/campaigns_*.yaml` | **Experimentally constructed** from configuration |

The generated traffic is **not real vehicle CAN traffic**. SUMO does not simulate CAN.

## Architecture

```
SUMO --TraCI--> SumoController --VehicleState(t)--> VehicleCanGenerator (one per vehicle, DBC + profile)
     --TxFrame(generation time)--> AttackEngine (inject / modify, ground truth attached)
     --> CanBusModel per vehicle (bit-exact duration, ID arbitration, queues, drops)
     --BusTx(tx_start, tx_end)--> CanTransport (SocketCAN, ECU and attacker sockets) --> vcanN
     --> CanCapture (separate socket per channel)
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
scripts/run_experiment.sh config/bus_check/experiment_sync.yaml  # benign bus-contention check
scripts/run_m2_suite.sh                         # all Milestone 2 attack runs (config/m2/)
scripts/run_suite.sh config/m3                  # Milestone 3 three-vehicle scenario matrix
sudo scripts/setup_vcan.sh 10 && scripts/run_suite.sh config/m3/scale   # 2/5/10-vehicle scaling
.venv/bin/python scripts/summarize_m3.py        # result tables from the saved sessions
scripts/run_suite.sh config/m4/delay            # Milestone 4 temporal-resolution runs
scripts/run_suite.sh config/m5/pilot            # Milestone 5 pilot matrix (fast mode, Parquet)
scripts/m5_benchmark.sh && .venv/bin/python scripts/m5_bench_report.py   # storage + speed benchmark
.venv/bin/python scripts/build_manifest.py      # data/manifest.jsonl (one line per session)
.venv/bin/python -m analysis.run_session <session_id> ...   # descriptors + evaluation labels
.venv/bin/python -m analysis.summarize_descriptors [--delay]  # sanity / alignment / resolution tables
.venv/bin/python -m src.validator --session <session_id>   # re-validate an existing session
.venv/bin/python -m pytest -q                              # unit + integration tests
```

## Repositories and sync

* Working repository (experiments run here): Ubuntu VM `~/fleet-can-dataset`.
* Mac clone: `.../University of Reading/CodeRepo/Generate-Dataset` (`origin` = the VM, `github` = GitHub).
* GitHub (private): `iyadsec/fleet-can-dataset`. Publish the VM's commits from the Mac with
  `scripts/sync_github.sh`. Generated data (`data/`) is never committed.

## Configuration

* `config/experiment.yaml`: IDs, seed, SUMO step and duration, transport, output.
* `config/vehicles.yaml`: vehicle **profiles** (DBC, message periods/offsets, signal mapping, synthetic
  model parameters) and the fleet (vehicle → profile; channel `vcan{index}` by list order).
  Profiles are separate from code, so heterogeneous fleets need only new profiles/DBCs.
* `config/vehicles.yaml` → `bus:` per profile: stuffing, queue_limit, drop_policy, node_queue_order;
  `nominal_bitrate` drives the bus model. `can.bus_model: logical | ideal` in the experiment file.
* `config/m2/attacks_*.yaml`: one attack per file (vehicle_01); `config/attacks.yaml` and
  `config/campaigns.yaml` are empty.

## Reproducibility

Each session stores the seed, SUMO command and version, Python and package versions, kernel, git commit
(and dirty flag), SHA-256 and a copy of every config/DBC/SUMO input, and the time-base definition.
With `realtime: true`, SUMO-timeline content is deterministic for a given seed. Capture timestamps
reflect host execution and are not.

## Limitations (model scope; first recorded in Milestone 2)

* Bus effects come from a logical model (exact frame length, ID arbitration, queues, drops), not
  from vCAN. Error frames, retransmission, bus-off and bit-level effects are not modelled; the same
  ID sent by two nodes at once is serialised instead of causing a bit error.
* Synthetic profiles A/B/C (Milestone 5); seeded switch-on offsets and ±1 % ECU release jitter (Milestone 5.1); no sensor noise.
* Campaign delays are exact on the logical timeline; transmission/capture layers are reported separately.
* Frame expiry uses one max age per bus (100 ms in Milestone 3 fleets), not per-message deadlines.
* Capture jitter depends on host/VM load; it is measured (`capture_offset_us`), never used as ground truth.
* vCAN interfaces must be recreated after reboot.
