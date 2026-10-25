# fleet-can-dataset

FLEETCAN-SIM is a **synthetic multi-vehicle CAN intrusion dataset**, and this repository holds the simulation testbed
that generated it. The dataset contains CAN traffic from fleets of simulated vehicles, with normal messages and
Flooding, Fuzzy, Malfunction and Replay attacks on single vehicles, on several vehicles independently, or as
coordinated campaigns. It is not real vehicle CAN data.

| Dataset | Content | GitHub release |
|---|---|---|
| **fleetcan-sim-2.0** (recommended) | 645 sessions, 8 vehicle profiles; campaign members share one attack procedure and start from one command with per-vehicle delivery latency; labelled hard negatives | `dataset-fleetcan-sim-2.0` |
| fleetcan-sim-dataset-1.0 | 835 sessions, 3 vehicle profiles; coordination is in timing only | `dataset-fleetcan-sim-dataset-1.0` |
| fleetcan-sim-scale-1.0 | The frozen raw generator output that fleetcan-sim-dataset-1.0 was exported from, including simulator-internal timing fields | `dataset-fleetcan-sim-scale-1.0` |

All three use the same log format. fleetcan-sim-dataset-1.0 and fleetcan-sim-scale-1.0 contain the same 835 sessions
and 444,333,222 CAN messages.

## Dataset: fleetcan-sim-2.0

### Description

In fleetcan-sim-2.0 coordination is visible in the traffic, not only in the labels:

* **Behaviour.** All members of a campaign execute the same attack procedure: target CAN ID or signal, rate, payload
  pattern and manipulation. The coordination strength is `identical`, `mild` (±5 % per member) or `strong` (±15 %).
  Each independent attack samples its own procedure.
* **Timing.** A campaign is commanded at one time. The command reaches each member after a log-normal delivery
  latency (median 80 ms, at most 1 s). Members then start by pattern: `broadcast`, `waves`, `sequential`, `bounded`,
  `partial`, `condition` (when the member's own vehicle reaches a speed or brakes) or `probe`.
* **Hard negatives.** Independent attacks that share a procedure (`S2_HardNegative_Behaviour`), that share
  coordinated-looking timing (`S2_HardNegative_Timing`), or both (`S2_HardNegative_Both`).
* **Benign variety.** Eight synthetic profiles (A–H) with 4–16 message types, event-driven messages with
  heartbeats, sensor noise and 0.5–5 % release jitter.
* **Floods.** Flood rates go up to 4,500 messages/s, which saturates the 500 kbit/s bus and causes frame drops.

| Property | Value |
|---|---|
| Sessions (capture files) | 645 = 129 configurations × 5 repetitions |
| CAN messages | 461,017,690 (Normal 420,615,395; Attack 40,402,295) |
| Vehicles per session | 20, 50 or 100 (37,750 vehicle traces) |
| Vehicle profiles | A–H (synthetic) |
| Attack types | Flooding, Fuzzy, Malfunction, Replay |
| Attack instances / campaigns | 8,480 / 1,665 (420 coordinated, 1,245 independent) |
| Scenarios | S0 normal (15 sessions), S1 isolated attack (60), S2 independent attacks (60), S2 hard negatives (3 × 30), S3 coordinated campaign (375), S3-mixed (30), S3-probe (15) |
| Session length | 16–69 s of simulated time |
| Format | gzip-compressed CSV and Parquet, same content; 1,298 files, 11.6 GB |
| Validation | every session passed all validator checks (frame, bus, attack, campaign and condition-trigger checks); every configured attack produced attack messages |

### Data format

The session logs have the same columns as fleetcan-sim-dataset-1.0:

```
Timestamp,Vehicle_ID,Arbitration_ID,DLC,Data,Class,SubClass,Attack_ID,Campaign_ID
```

`Class`, `SubClass`, `Attack_ID` and `Campaign_ID` are labels; remove them from any detector input. The summary tables
add the procedure and controller fields:

| File | One row per | Main columns |
|---|---|---|
| `sessions.csv` | session | scenario, attack type, fleet size, campaign size, timing pattern, coordination strength, hard-negative type, command time, duration, seed, counts, file paths, generator version and commit |
| `vehicles.csv` | vehicle | profile, ECU switch-on time, attacked, message counts |
| `attacks.csv` | attack | vehicle, type, campaign, start/end, rate, first/last attack message, `Procedure_ID`, strength, hard-negative type, arm/trigger time, `Saturated_Values`, parameters (JSON) |
| `campaigns.csv` | campaign | coordinated, type, controller pattern, `Procedure_ID`, strength, hard-negative type, trigger, vehicles, command time |
| `campaign_members.csv` | member | start/end, relative delay, pattern offset, delivery latency, arm/trigger time |
| `replay_links.csv.gz` | replayed message | original and replay timestamps |

### File structure

```
fleetcan-sim-2.0/
├── README.md                full description (scenarios, procedures, timing patterns, columns, tables)
├── sessions.csv  vehicles.csv  attacks.csv  campaigns.csv  campaign_members.csv  replay_links.csv.gz
├── SHA256SUMS
├── CSV/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.csv.gz
└── Parquet/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.parquet
```

Scenario folders are `S0_Normal`, `S1_Isolated_Attack`, `S2_Independent_Attacks`, `S2_HardNegative_Behaviour`,
`S2_HardNegative_Timing`, `S2_HardNegative_Both`, `S3_Coordinated_Campaign`, `S3_Mixed` and `S3_Probe`. A Session_ID
such as `FCS2_N050_S3_replay_K010_waves_mild_R3` gives the fleet size, scenario, attack type, campaign size, timing
pattern, strength and repetition.

### How to download

You need about 25 GB free (the download is deleted after a successful restore) and the tools from step 2 of the
fleetcan-sim-dataset-1.0 instructions below. Then:

```bash
git clone https://github.com/iyadsec/fleet-can-dataset.git
cd fleet-can-dataset
scripts/download_fleetcan_sim_2_0.sh ~/fleetcan_data
```

The script downloads the 8 release parts and checks them against the committed checksums
(`results/dataset_release/fleetcan-sim-2.0/`). It then extracts them and verifies every file against `SHA256SUMS`.
The last line reads `SUCCESS: fleetcan-sim-2.0 restored to ...`.

```python
import pandas as pd
df = pd.read_parquet("fleetcan_data/fleetcan-sim-2.0/Parquet/S3_Coordinated_Campaign/Replay/N050/"
                     "FCS2_N050_S3_replay_K005_sequential_mild_R1.parquet")
```

### Rebuild

The experimental matrix is `configs/fleetcan_sim_2_0.yaml` (SHA-256
`0bb2be29dfce6046600d0161c2d1e58508930e731a10f186e5d6eacf82fcc6e4`), generated by `scripts/fcs2_design.py`.
`scripts/run_fcs2_generation.py --all` regenerates the sessions, and `scripts/export_fleetcan_sim_2_0.py` exports
them. The design is described in [docs/m12_fleetcan_sim_2_0_design.md](docs/m12_fleetcan_sim_2_0_design.md).

## Dataset: fleetcan-sim-dataset-1.0

### Description

| Property | Value |
|---|---|
| Sessions (capture files) | 835 = 167 configurations × 5 repetitions |
| CAN messages | 444,333,222 (Normal 395,777,880; Attack 48,555,342) |
| Vehicles per session | 10, 20, 50 or 100 (48,150 vehicle traces) |
| Vehicle profiles | A, B, C (synthetic CAN-ID sets, periods and layouts) |
| Attack types | Flooding, Fuzzy, Malfunction, Replay |
| Attack instances / campaigns | 12,180 / 1,895 (575 coordinated, 1,320 independent) |
| Scenarios | S0 normal (20 sessions), S1 isolated attack (60), S2 independent attacks (180), S3 coordinated campaign (530), S3-mixed (30), S3-probe (15) |
| Format | gzip-compressed CSV and Parquet, same content |

### Data format

Every session log has the same header, following the HCRL/OCSLab car-hacking datasets, plus three fleet columns:

```
Timestamp,Vehicle_ID,Arbitration_ID,DLC,Data,Class,SubClass,Attack_ID,Campaign_ID
0.036436734,vehicle_010,101,6,00 19 01 F9 00 00,Normal,Normal,,
10.000254000,vehicle_004,000,8,00 00 00 00 00 00 00 00,Attack,Flooding,C_S3_FLOODING_vehicle_004,C_S3_FLOODING
```

| Column | Description |
|---|---|
| `Timestamp` | seconds on the session's shared simulation clock (time the message was complete on the bus) |
| `Vehicle_ID` | vehicle / CAN bus |
| `Arbitration_ID` | CAN identifier, hexadecimal |
| `DLC` | data length code (0–8) |
| `Data` | data field, hexadecimal bytes separated by a space |
| `Class` | `Normal` or `Attack` |
| `SubClass` | `Normal`, `Flooding`, `Fuzzy`, `Malfunction` or `Replay` |
| `Attack_ID`, `Campaign_ID` | attack instance and campaign of an attack message (empty for normal messages) |

### File structure

```
fleetcan-sim-dataset-1.0/
├── README.md                full description (scenarios, attacks, columns, tables)
├── sessions.csv             one row per session: scenario, attack type, fleet size, campaign size, timing, counts, file paths
├── vehicles.csv             one row per vehicle: profile, ECU switch-on time, attacked, message counts
├── attacks.csv              one row per attack instance: vehicle, type, campaign, configured start/end, rate, counts
├── campaigns.csv            one row per campaign: coordinated or not, type, delay pattern, vehicles, start/end
├── campaign_members.csv     one row per campaign member: vehicle, attack, start/end, relative delay
├── replay_links.csv.gz      one row per replayed message: original and replay timestamps
├── SHA256SUMS
├── CSV/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.csv.gz
└── Parquet/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.parquet
```

Scenario folders are `S0_Normal`, `S1_Isolated_Attack`, `S2_Independent_Attacks`, `S3_Coordinated_Campaign`,
`S3_Mixed` and `S3_Probe`. The full description, including attack definitions and timing patterns, is in the
dataset's own `README.md`.

### How to download (step by step)

**Step 1: check free disk space.** You need about 22 GB free. The download is deleted after a successful restore.

**Step 2: install the tools.**

```bash
# Ubuntu / Debian
sudo apt-get install -y git zstd tar gh python3
# macOS (Homebrew)
brew install git zstd gh
```

**Step 3: clone this repository.** The clone contains the download script and the reference checksums, not the
data.

```bash
git clone https://github.com/iyadsec/fleet-can-dataset.git
cd fleet-can-dataset
```

**Step 4: download, extract and verify.**

```bash
scripts/download_fleetcan_sim_dataset_1_0.sh ~/fleetcan_data
```

The script downloads the release parts and checks them against the committed checksums. It then extracts them and
verifies every file against `SHA256SUMS`. The last line reads
`SUCCESS: fleetcan-sim-dataset-1.0 restored to ...`. On any mismatch it prints `FAIL: ...` and restores nothing.

You can also download the parts by hand from the release page.

**Step 5: load a session.**

```python
import pandas as pd
df = pd.read_csv("fleetcan_data/fleetcan-sim-dataset-1.0/CSV/S3_Coordinated_Campaign/Replay/N050/"
                 "FCS10_N050_S3_replay_K010_partial_R3.csv.gz",
                 dtype={"Arbitration_ID": str, "Data": str}, keep_default_na=False)
```

### Rebuild from the source dataset

```bash
.venv/bin/python scripts/export_fleetcan_sim_dataset.py OUTPUT_DIR --workers 2   # needs fleetcan-sim-scale-1.0 under data/
```

## Dataset: fleetcan-sim-scale-1.0 (frozen source)

This is the raw generator output: per session `raw/` CAN frames with all simulator timing fields, `ground_truth/`,
`metadata/` and `validation/`. It has 26,740 files, 21.7 GB, and the 9-part archive is 13.87 GB. `SHA256SUMS`
SHA-256: `5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70`.

To restore it, with about 35 GB free:

```bash
scripts/download_fleetcan_sim_scale_1_0.sh ~/fleetcan_data
```

More detail: [docs/dataset_download.md](docs/dataset_download.md).

## Documentation

| Topic | Location |
|---|---|
| Design | `docs/m9_expanded_dataset_design.md`, `docs/m9_1_matrix_freeze.md` |
| Generation report | `docs/m10_expanded_dataset_generation.md` |
| Download (source dataset) | `docs/dataset_download.md` |
| Full procedure and code map | `docs/PROCEDURE.md` |
| Freeze records and hash lists | `results/datasets/` |
| Release manifests | `results/dataset_release/` |

The dataset files are distributed as release assets, not in Git.

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
.venv/bin/python -m analysis.run_session <session_id> ...   # development descriptor layer (not part of the dataset)
.venv/bin/python -m analysis.summarize_descriptors [--delay]  # sanity / alignment / resolution tables
.venv/bin/python -m src.validator --session <session_id>   # re-validate an existing session
.venv/bin/python -m pytest -q                              # unit + integration tests
```

## Repositories and sync

* Working repository (experiments run here): Ubuntu VM `~/fleet-can-dataset`.
* Mac clone: `.../University of Reading/CodeRepo/Generate-Dataset` (`origin` = the VM, `github` = GitHub).
* GitHub (public): `iyadsec/fleet-can-dataset`. Publish the VM's commits from the Mac with
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

## Commit IDs

The history of this repository was rewritten on 2026-10-24. Only commit messages changed; every file, author and
date is the same. The frozen source dataset records the earlier generator commit IDs `f212877` and `7b65963`.
[docs/commit_id_map.csv](docs/commit_id_map.csv) maps every earlier ID to its current one: `f212877` →
`c9b72c5` and `7b65963` → `6ff1879`.
