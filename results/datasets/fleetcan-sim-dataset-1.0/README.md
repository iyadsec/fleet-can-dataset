# FLEETCAN-SIM: Multi-Vehicle CAN Intrusion Dataset (fleetcan-sim-dataset-1.0)

## 1. Description

FLEETCAN-SIM is a **synthetic** Controller Area Network (CAN) dataset of **fleets of vehicles**. It contains normal
traffic and four types of injected attacks. Attacks are either launched on single vehicles, or on several vehicles
independently or as coordinated campaigns.

The traffic was produced by a simulation testbed:
* SUMO (v1.28) provides vehicle mobility.
* Python/TraCI orchestrates the simulation.
* CAN messages are generated from the simulated vehicle state using three synthetic vehicle profiles.
* A logical CAN bus model at 500 kbit/s handles timing and arbitration, one bus per vehicle.
* Frames are transmitted and captured over Linux SocketCAN virtual CAN (vCAN).

All vehicles in a session share one simulation clock.

**This is not real vehicle data.** It contains no production ECU captures, the CAN IDs and signal layouts are defined
by the project, and the logical bus model is not a physical CAN bus.

| Property | Value |
|---|---|
| Sessions (capture files) | 835 = 167 configurations × 5 repetitions |
| CAN messages | 444,333,222 (Normal 395,777,880; Attack 48,555,342) |
| Vehicles per session | 10, 20, 50 or 100 (48,150 vehicle traces in total) |
| Vehicle profiles | A, B, C (synthetic) |
| Attack types | Flooding, Fuzzy, Malfunction, Replay |
| Attack instances / campaigns | 12,180 / 1,895 (575 coordinated, 1,320 independent) |
| Session length | 20–60 s of simulated time |

### Scenarios

| Folder | Scenario | Sessions | Content |
|---|---|---|---|
| `S0_Normal` | S0 | 20 | attack-free traffic |
| `S1_Isolated_Attack` | S1 | 60 | one attack on one vehicle |
| `S2_Independent_Attacks` | S2 | 180 | 5 or 10 unrelated attacks of one type on different vehicles (starts near-simultaneous, 0.5 s staggered or 10 s separated) |
| `S3_Coordinated_Campaign` | S3 | 530 | one coordinated campaign on K vehicles (K = 2, 5, 10, 20, 50, 100) |
| `S3_Mixed` | S3-mixed | 30 | a coordinated campaign plus unrelated attacks of the same type in the same fleet |
| `S3_Probe` | S3-probe | 15 | coordinated replay on 2 vehicles started 0.1 s apart |

Coordinated campaign timing patterns:
* `simultaneous`: all members start at 10 s;
* `small`, `medium`, `large`: members start 0.5 s, 2 s or 10 s apart;
* `partial`: members start 5 s apart with 10 s attacks, so consecutive members overlap;
* `bounded_spread_0p5s`, `bounded_spread_2s`, `bounded_spread_10s`: all K members start within 0.5, 2 or 10 s;
* `probe`: 0.1 s apart.

Attacks start at or after 10 s.

## 2. Class

* **Normal:** periodic messages of the vehicle's ECUs.
  * Profile A: 5 CAN IDs, about 350 messages/s.
  * Profile B: 4 CAN IDs, about 270 messages/s.
  * Profile C: 5 CAN IDs, about 350 messages/s.
  * Periods are 10, 20 or 50 ms with ±1 % jitter. Each vehicle's ECUs switch on at a random time between 0 and 1 s.
* **Attack:** messages injected on the target vehicle's bus.
  * **Flooding:** CAN ID `000` (highest priority), DLC 8, all-zero data, 1600 messages/s.
  * **Fuzzy:** random CAN ID (`000`–`7FF`), random DLC (0–8) and random data, 1000 messages/s.
  * **Malfunction:** the vehicle-speed message of the target's profile, injected with a constant value of 250 km/h at
    100 messages/s; the genuine message continues.
  * **Replay:** the vehicle's own normal messages from the 5 s before the attack (10 s for `partial`) re-sent in
    the original order and timing.

  Attacks last 5 s, or 10 s in `partial`.

## 3. Data format

Each session is one CAN log, provided as gzip-compressed CSV (`CSV/`) and as Parquet (`Parquet/`) with identical
content. All logs have the same header:

```
Timestamp,Vehicle_ID,Arbitration_ID,DLC,Data,Class,SubClass,Attack_ID,Campaign_ID
0.036436734,vehicle_010,101,6,00 19 01 F9 00 00,Normal,Normal,,
10.000254000,vehicle_004,000,8,00 00 00 00 00 00 00 00,Attack,Flooding,C_S3_FLOODING_vehicle_004,C_S3_FLOODING
```

| Column | Description |
|---|---|
| `Timestamp` | Time in seconds on the session's simulation clock, shared by all vehicles, 9 decimals. It marks when the message was complete on the vehicle's CAN bus. Rows are in time order. |
| `Vehicle_ID` | Vehicle, and therefore CAN bus, that carried the message |
| `Arbitration_ID` | CAN identifier in hexadecimal (11-bit, 3 digits) |
| `DLC` | Data length code (0–8) |
| `Data` | CAN data field in hexadecimal, bytes separated by a space (empty when DLC = 0) |
| `Class` | `Normal` or `Attack` |
| `SubClass` | Attack type: `Flooding`, `Fuzzy`, `Malfunction` or `Replay`; `Normal` for normal messages |
| `Attack_ID` | Attack instance that injected the message (empty for normal messages); see `attacks.csv` |
| `Campaign_ID` | Campaign the attack belongs to (empty for normal messages); see `campaigns.csv` |

Summary tables (CSV):

| File | One row per | Columns |
|---|---|---|
| `sessions.csv` | session | Session_ID, Configuration_ID, Repetition, Scenario, Attack_Type, Fleet_Size, Campaign_Size, Timing_Pattern, Independent_Timing, Member_Selection, Duration_s, Seed, Vehicles, Attacked_Vehicles, Frames, Normal_Frames, Attack_Frames, Attacks, Campaigns, CSV_File, Parquet_File |
| `vehicles.csv` | vehicle in a session | Session_ID, Vehicle_ID, Profile, ECU_Switch_On_s, Attacked, Frames, Attack_Frames |
| `attacks.csv` | attack instance | Session_ID, Attack_ID, Vehicle_ID, Attack_Type, Campaign_ID, Start_s, End_s (configured), Rate_Hz, Attack_Frames, First_Attack_Frame_s, Last_Attack_Frame_s, Parameters |
| `campaigns.csv` | campaign | Session_ID, Campaign_ID, Coordinated, Attack_Type, Delay_Pattern, Vehicles, Start_s, End_s |
| `campaign_members.csv` | vehicle in a campaign | Session_ID, Campaign_ID, Vehicle_ID, Attack_ID, Start_s, End_s, Relative_Delay_s |
| `replay_links.csv.gz` | replayed message | Session_ID, Attack_ID, Vehicle_ID, Arbitration_ID, Original_Timestamp, Replay_Timestamp |

`Coordinated = True` marks a coordinated campaign. Independent and isolated attacks each form their own campaign
with `Coordinated = False`.

## 4. File structure

```
fleetcan-sim-dataset-1.0/
├── README.md
├── sessions.csv  vehicles.csv  attacks.csv  campaigns.csv  campaign_members.csv  replay_links.csv.gz
├── SHA256SUMS
├── CSV/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.csv.gz
└── Parquet/<Scenario>/<Attack type>/N<fleet size>/<Session_ID>.parquet
```

Example: `CSV/S3_Coordinated_Campaign/Replay/N050/FCS10_N050_S3_replay_K010_partial_R3.csv.gz`.

The Session_ID encodes the configuration and the repetition:
* fleet size `N050`;
* scenario `S3`;
* attack type `replay`;
* campaign size `K010`;
* timing pattern `partial`;
* repetition `R3`.

Sessions named `FCS10_G0_N010_*` use 10 vehicles, and their campaign members are the first K vehicles.

## 5. Notes for users

* `Class`, `SubClass`, `Attack_ID` and `Campaign_ID` are labels. Remove them from any detector input.
* `Vehicle_ID` identifies the bus. Message timing within one vehicle is only meaningful per vehicle.
* No data was removed, filtered or modified after generation. No train/test split is predefined.
* Known simplifications:
  * the logical bus never dropped a frame;
  * normal traffic is strictly periodic, with little payload variability;
  * vehicles were simulated on a 4×4 grid road network;
  * flooding does not saturate the bus.

## 6. Download and integrity

The dataset is published as the GitHub release `dataset-fleetcan-sim-dataset-1.0` of
https://github.com/iyadsec/fleet-can-dataset (split archive parts). The repository script
`scripts/download_fleetcan_sim_dataset_1_0.sh DESTINATION` downloads, joins, extracts and verifies them. To check a
copy:

```
shasum -a 256 -c SHA256SUMS      # or: sha256sum -c SHA256SUMS
```

## 7. Citation

Iyad Atieh, "FLEETCAN-SIM: Multi-Vehicle CAN Intrusion Dataset (fleetcan-sim-dataset-1.0)", University of Reading,
2026. https://github.com/iyadsec/fleet-can-dataset

## 8. Contact

Iyad Atieh, University of Reading.
