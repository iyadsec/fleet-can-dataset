# Dataset schema (Milestone 2)

Session layout: `data/{raw,ground_truth,metadata,validation}/<session_id>/`,
`session_id = <experiment_id>_<UTC start YYYYMMDDTHHMMSSZ>`.
Scenarios: **S0** benign fleet; **S1** isolated single-vehicle attack. S2–S4 (independent and
coordinated multi-vehicle) are reserved for later milestones.

## raw/can_frames.csv (observations; one row per captured frame, in modelled bus order)

| Column | Meaning |
|---|---|
| frame_uid | `<session_id>-F<9 digits>`; join key to ground truth |
| session_id, scenario_id | run and scenario identifiers |
| timestamp | **generation** time, SUMO-timeline seconds with ns precision (= `sim_time_ns`/1e9) |
| sim_time_ns | generation time, integer ns |
| transmission_timestamp | modelled end of frame on the bus (= `tx_end_ns`/1e9), SUMO timeline |
| tx_start_ns, tx_end_ns | modelled bus occupancy interval |
| queue_delay_us | `tx_start − generation` (modelled queueing + arbitration) |
| frame_bits | exact frame length in bits incl. stuffing and intermission |
| capture_time_ns | kernel receive time on the vCAN capture socket (CLOCK_REALTIME) |
| capture_offset_us | `capture_time − (wall_anchor + tx_end)`: host/VM jitter |
| vehicle_id, channel | `vehicle_01` … and its vCAN interface |
| can_id, dlc, payload | captured 11-bit ID (hex), DLC, payload (uppercase hex, 2×DLC chars) |

## raw/vehicle_states.csv
SUMO mobility per step: timestamp, sim_time_ns, vehicle_id, speed_mps, accel_mps2, x_m, y_m,
angle_deg, lane_id, edge_id, route_id.

## ground_truth/ (separate from observations)

| File | Content |
|---|---|
| frame_labels.csv | frame_uid, frame_label (`benign`/`malicious`), attack_type, campaign_id (`None` in M2), attack_id, frame_source (`benign_generator` / `attack_injection` / `attack_modification`), message_name |
| dropped_frames.csv | frames generated but never transmitted (bus queue overflow): node, generation and drop time, reason, ID, payload, label, attack_id |
| attacks.csv | per attack: type, vehicle, channel, configured start/end, requested rate, generated / transmitted / dropped / captured, observed rate, per-attack seed, parameters (JSON) |
| attack_frames.csv | per captured attack frame: fuzzy mode; malfunction message, signal, **expected (legitimate) value**, **malicious value**, original payload (replace mode) |
| replay_map.csv | per replayed frame: replay_frame_uid, **original_frame_uid**, source and target vehicle, CAN ID, original generation and transmission timestamps, replay generation and transmission timestamps, original and replayed payload |
| campaigns.csv | header only until Milestone 3 |

Labels come only from frame provenance (which component created the frame), never from timing or
a detector. `frame_source = attack_modification` marks a legitimate ECU frame altered in place by
a malfunction attack; its original payload is in attack_frames.csv.

## metadata/
`metadata.json` (seeds, versions, time base, bus model config and totals, attack runtime summary,
git commit, config hashes), `bus_stats.json` (per channel: utilization per 100 ms bin and overall,
queue-delay statistics for benign and attack frames, max queue length per node, drops; per attack:
utilization and benign delay/drops during the attack vs an equally long baseline before it),
`config_snapshot/` (every config, DBC and SUMO input file as used).

## Synthetic CAN model (`dbc/generic_vehicle.dbc`)
All messages: 11-bit ID, DLC 8, little-endian signals, unused bits 0, 4-bit rolling counter in
bits 56–59. **IDs are synthetic, not OEM.** Transmitting nodes (DBC senders): ECM 0x100, ABS 0x120,
APP 0x150, BCM 0x180, EPS 0x200.

| ID | Message | Period / offset | Signals (start\|len, scale, unit) | From SUMO |
|---|---|---|---|---|
| 0x100 | ENGINE_STATUS | 10 ms / 0 ms | EngineRPM 0\|16 ×0.25 rpm; Gear 16\|4 | speed → gear (upshift 15/30/50/70/90 km/h) → RPM = wheel rpm × ratio × 3.9, min 800 |
| 0x120 | VEHICLE_SPEED | 20 ms / 1 ms | VehicleSpeed 0\|16 ×0.01 km/h; LongAccel 16\|16 s ×0.001 m/s² | speed × 3.6; acceleration |
| 0x150 | ACCEL_PEDAL | 20 ms / 3 ms | AccelPedalPos 0\|8 ×0.4 % | a ≥ 0: 15 % + 85 % × a/2.6 |
| 0x180 | BRAKE_STATUS | 20 ms / 5 ms | BrakeActive 0\|1; BrakeDecel 8\|16 ×0.001 m/s² | a < −0.3 m/s² |
| 0x200 | STEERING | 10 ms / 7 ms | SteeringAngle 0\|16 s ×0.1 deg; YawRate 16\|16 s ×0.01 deg/s | yaw = −Δheading/Δt; steer = atan(2.7·yaw/v)·15 |

## Attack families (config/m2/attacks_*.yaml)

| Type | Parameters | Frames |
|---|---|---|
| flooding | can_id, rate_hz, dlc, payload (zeros / random / hex) | ATTACKER node; generation every 1/rate inside the window |
| fuzzy | mode `id` / `payload` / `both`, rate_hz, id_range, can_id (payload mode), dlc (int or random), payload (id mode) | ATTACKER; per-attack seeded RNG |
| malfunction | mode `inject` / `replace`, message, signal, manipulation `constant`/`offset`/`scale`/`ramp`, rate_hz + phase_ms (inject) | inject: ATTACKER frames copying the latest legitimate frame with the target signal changed and the next counter value. replace: the ECU's own frames inside the window carry the wrong value |
| replay | source_vehicle, window_s, can_ids, time_offset_ms | ATTACKER re-sends benign frames whose bus end time is in the window; replay time = start + (original tx_end − window start) + offset |
