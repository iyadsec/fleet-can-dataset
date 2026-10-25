# FLEETCAN-SIM 2.0: Multi-Vehicle CAN Intrusion Dataset with Behavioural and Temporal Coordination

## 1. Description

FLEETCAN-SIM 2.0 is a **synthetic** Controller Area Network (CAN) dataset of **fleets of vehicles**. It contains
normal traffic and four types of injected attacks, launched on single vehicles, on several vehicles independently,
or as **coordinated campaigns**.

Coordination is present in the CAN traffic itself:
* **Behaviour.** All members of a campaign execute the same attack procedure: target CAN ID or signal, rate, payload
  pattern and manipulation. Each independent attack uses its own procedure.
* **Timing.** A campaign is started by a command whose delivery delay differs per vehicle. Members start
  simultaneously, in waves, sequentially, within a bounded spread, with partial overlap, or when their own vehicle
  reaches a driving condition.

Labelled **hard negatives** separate the two kinds of evidence: independent attacks that share a procedure, that share
coordinated-looking timing, or both.

The traffic was produced by a simulation testbed:
* SUMO 1.28 provides vehicle mobility, orchestrated through Python/TraCI.
* CAN messages are generated from the simulated vehicle state by **eight synthetic vehicle profiles (A–H)**.
* A logical CAN bus model at 500 kbit/s, one bus per vehicle, handles timing and arbitration.
* Frames are transmitted and captured over Linux SocketCAN virtual CAN (vCAN).

All vehicles of a session share one simulation clock.

**This is not real vehicle data.** It contains no production ECU captures; the CAN IDs and signal layouts are defined
by the project; and the logical bus model is not a physical CAN bus.

| Property | Value |
|---|---|
| Sessions (capture files) | 645 = 129 configurations × 5 repetitions |
| CAN messages | 461,017,690 (Normal 420,615,395; Attack 40,402,295) |
| Vehicles per session | 20, 50 or 100 (37,750 vehicle traces) |
| Vehicle profiles | A–H (synthetic; 4–16 message types each, periods 10 ms – 1 s, event-driven messages, sensor noise, 0.5–5 % release jitter) |
| Attack types | Flooding, Fuzzy, Malfunction, Replay |
| Attack instances / campaigns | 8,480 / 1,665 (420 coordinated, 1,245 independent) |
| Session length | 16–69 s of simulated time |

### Scenarios

| Folder | Scenario | Sessions | Content |
|---|---|---|---|
| `S0_Normal` | S0 | 15 | attack-free traffic |
| `S1_Isolated_Attack` | S1 | 60 | one attack on one vehicle |
| `S2_Independent_Attacks` | S2-diverse | 60 | 5 or 10 unrelated attacks of one type; each has its own procedure and its own start time |
| `S2_HardNegative_Behaviour` | S2-behaviour | 30 | unrelated attacks that share one procedure; their start times are independent |
| `S2_HardNegative_Timing` | S2-timing | 30 | unrelated attacks with their own procedures, started with coordinated-looking timing |
| `S2_HardNegative_Both` | S2-both | 30 | unrelated attacks with a shared procedure **and** coordinated-looking timing; indistinguishable from a campaign by construction (control) |
| `S3_Coordinated_Campaign` | S3 | 375 | one coordinated campaign on K vehicles (K = 2, 5, 10, 20, 50, 100) |
| `S3_Mixed` | S3-mixed | 30 | a coordinated campaign plus unrelated attacks of the same type, each with its own procedure |
| `S3_Probe` | S3-probe | 15 | 2-vehicle campaign with members 0.1 s apart |

### Coordination

* **Command and delivery.** A campaign is commanded at a time T between 8 and 20 s. The command reaches each member
  after a delivery latency drawn from a log-normal distribution (median 80 ms, σ_log 0.6, at most 1 s).
* **Timing patterns** (member start = T + pattern offset + latency):

| Pattern | Offsets |
|---|---|
| `broadcast` | all members at once |
| `waves` | 2–4 groups, 1, 3 or 5 s apart |
| `sequential` | 0.5 or 2 s steps (K ≤ 20) |
| `bounded` | all K members within 0.5, 2 or 10 s (K ≥ 20) |
| `partial` | 5 s steps with 10 s attacks (K = 5) |
| `condition` | a member starts when its own vehicle reaches a speed (30, 35 or 40 km/h) or brakes (≤ −1 m/s²), at most 20 s after the command |
| `probe` | 0.1 s apart |

* **Coordination strength:**
  * `identical`: same parameters and the same random stream for every member;
  * `mild`: each member varies rate and values by up to ±5 %;
  * `strong`: each member varies them by up to ±15 %.

## 2. Class

* **Normal:** messages of the vehicle's ECUs.
  * Periodic messages at 10–1000 ms.
  * Event-driven messages (gear changes, braking, indicators, door locks), each with a heartbeat.
  * Sensor noise on physical signals.
  * Each vehicle's ECUs switch on at a random time between 0 and 1 s.
* **Attack:** messages injected on (or, for replace-mode malfunction, modified on) the target vehicle's bus.
  * Every attack follows a procedure sampled from the space below.
  * Its identifier, `Procedure_ID`, appears in `attacks.csv` and `campaigns.csv`.

| Type | Procedure parameters |
|---|---|
| **Flooding** | CAN ID `000`–`07F`; 400, 800, 1,600, 3,000 or 4,500 messages/s (4,500 saturates the bus and causes frame drops); DLC 2, 4 or 8; fixed or random payload |
| **Fuzzy** | random IDs within a sub-range of `000`–`7FF`, random payloads for one target signal's message, or both; 200–1,500 messages/s; fixed or random DLC |
| **Malfunction** | target signal: vehicle speed, engine RPM, accelerator, brake or steering; injected alongside the genuine message or replacing it; value constant, offset, scaled or ramped. Values beyond a signal's range saturate at the range limit, counted in `Saturated_Values`. |
| **Replay** | the vehicle's own messages from a 1–8 s window before the attack, all IDs or 2–4 signals' IDs, re-sent with the original timing plus 0.1–5 ms |

Attack durations are 2–10 s, or 10 s for `partial`.

## 3. Data format

Each session is one CAN log, as gzip-compressed CSV (`CSV/`) and as Parquet (`Parquet/`) with identical content. The
format is the same as fleetcan-sim-dataset-1.0:

```
Timestamp,Vehicle_ID,Arbitration_ID,DLC,Data,Class,SubClass,Attack_ID,Campaign_ID
```

| Column | Description |
|---|---|
| `Timestamp` | seconds on the session's shared simulation clock, 9 decimals; the time the message was complete on the vehicle's bus. Rows are in time order. |
| `Vehicle_ID` | vehicle / CAN bus |
| `Arbitration_ID` | CAN identifier, hexadecimal (11-bit, 3 digits) |
| `DLC` | data length code (0–8) |
| `Data` | data field, hexadecimal bytes separated by a space (empty when DLC = 0) |
| `Class` | `Normal` or `Attack` |
| `SubClass` | `Normal`, `Flooding`, `Fuzzy`, `Malfunction` or `Replay` |
| `Attack_ID` / `Campaign_ID` | attack instance and campaign of an attack message (empty for normal messages and isolated attacks) |

Summary tables:

| File | One row per | Main columns |
|---|---|---|
| `sessions.csv` | session | Scenario, Attack_Type, Fleet_Size, Campaign_Size, Timing_Pattern, Coordination_Strength, Hard_Negative, Independent_Attacks, Command_Time_s, Duration_s, Seed, message counts, file paths, Generator_Version / Generator_Commit |
| `vehicles.csv` | vehicle in a session | Profile, ECU_Switch_On_s, Attacked, message counts |
| `attacks.csv` | attack | Vehicle, type, campaign, configured Start_s / End_s, rate, first and last attack message, Procedure_ID, strength, hard-negative type, Arm_s / Trigger_s / Trigger_Timeout (condition pattern), Saturated_Values, Parameters (JSON) |
| `campaigns.csv` | campaign | Coordinated, type, Controller_Pattern, Procedure_ID, strength, hard-negative type, Trigger, vehicles, Command_s, End_s |
| `campaign_members.csv` | member | Start_s, End_s, Relative_Delay_s, Pattern_Offset_s, Delivery_Latency_s, Arm_s, Trigger_s |
| `replay_links.csv.gz` | replayed message | original and replay timestamps |

## 4. File structure

```
fleetcan-sim-2.0/
├── README.md
├── sessions.csv  vehicles.csv  attacks.csv  campaigns.csv  campaign_members.csv  replay_links.csv.gz
├── SHA256SUMS
├── CSV/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.csv.gz
└── Parquet/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.parquet
```

The Session_ID encodes the configuration. For example, `FCS2_N050_S3_replay_K010_waves_mild_R3` means:
* fleet size 50;
* coordinated campaign;
* replay;
* 10 members;
* waves pattern;
* mild strength;
* repetition 3.

## 5. Notes for users

* `Class`, `SubClass`, `Attack_ID` and `Campaign_ID` are labels. Remove them from any detector input. The same applies
  to the `sessions.csv` metadata (scenario, campaign size, hard-negative type).
* No data were removed, filtered or modified after generation. No train/test split is predefined.
* The hard-negative scenarios are deliberate. `S2-both` cannot be separated from a coordinated campaign using traffic
  and timing alone, and should be reported separately.
* **Known simplifications:**
  * the vehicles drive on a 4×4 grid road network;
  * the logical bus has no error frames or bit errors;
  * frame drops occur only under saturating floods;
  * behavioural variety is limited to the 8 synthetic profiles.
* **Generation:**
  * Generated with generator 2.0.0. Sessions produced before commit e1849c7 record the version string "1.3.0" (343
    sessions); the exact code commit of every session is in `sessions.csv` (`Generator_Commit`).
  * The experimental matrix was frozen before generation; its final SHA-256 is `0bb2be29dfce6046600d0161c2d1e58508930e731a10f186e5d6eacf82fcc6e4`.
  * Two design corrections each changed only the sessions they affected, and those were regenerated: 5 sessions
    (malfunction value range) and 15 sessions (isolated replay source vehicle). 111 sessions were generated under the
    first matrix SHA with configurations identical to the final matrix.

## 6. Download and integrity

Published as the GitHub release `dataset-fleetcan-sim-2.0` of https://github.com/iyadsec/fleet-can-dataset (split
archive parts). The repository script `scripts/download_fleetcan_sim_2_0.sh DESTINATION` downloads, extracts and
verifies them. To check a copy: `shasum -a 256 -c SHA256SUMS`.

## 7. Citation

Iyad Atieh, "FLEETCAN-SIM 2.0: Multi-Vehicle CAN Intrusion Dataset with Behavioural and Temporal Coordination",
University of Reading, 2026. https://github.com/iyadsec/fleet-can-dataset
