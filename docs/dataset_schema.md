# Dataset schema (Milestone 1)

Session layout: `data/{raw,ground_truth,metadata,validation}/<session_id>/`.
`session_id = <experiment_id>_<UTC start YYYYMMDDTHHMMSSZ>`. Scenario IDs: S0 benign fleet;
S1–S4 are reserved for later milestones (isolated, independent, coordinated strong/weak).

## raw/can_frames.csv (observations; one row per captured frame)

| Column | Meaning |
|---|---|
| frame_uid | `<session_id>-F<9 digits>`; join key to ground truth |
| session_id, scenario_id | run and scenario identifiers |
| timestamp | SUMO-timeline seconds, ns precision (`sim_time_ns / 1e9`), common to all vehicles |
| sim_time_ns | integer ns on the SUMO timeline: scheduled transmission time of the frame |
| capture_time_ns | Linux kernel receive timestamp on the vCAN capture socket (CLOCK_REALTIME) |
| capture_offset_us | `capture_time_ns − (wall_anchor_ns + sim_time_ns)`: execution/transport jitter |
| vehicle_id | `vehicle_01`… (SUMO vehicle ID, persistent) |
| channel | vCAN interface the frame was captured on |
| can_id | captured 11-bit ID, hex (`0x120`) |
| dlc | captured DLC (0–8) |
| payload | captured payload, uppercase hex, `2 × dlc` characters |

## raw/vehicle_states.csv (SUMO mobility per step)

timestamp, sim_time_ns, vehicle_id, speed_mps, accel_mps2, x_m, y_m, angle_deg, lane_id, edge_id, route_id.

## ground_truth/frame_labels.csv

frame_uid, session_id, frame_label (`benign`), attack_type (`None`), campaign_id (`None`), attack_id
(`None`), frame_source (`benign_generator`), message_name.

## ground_truth/campaigns.csv (header only in S0)

campaign_id, scenario_id, session_id, attack_type, vehicle_id, campaign_start, campaign_end,
vehicle_attack_start, vehicle_attack_end, relative_delay, coordinated, delay_pattern.

## Synthetic CAN model (`dbc/generic_vehicle.dbc`)

All messages: 11-bit ID, DLC 8, little-endian (Intel) signals, unused bits zero, 4-bit rolling counter
in bits 56–59. **IDs are synthetic, not OEM.**

| ID | Message | Period / offset | Signals (start bit \| length, scale, unit) | Derived from SUMO |
|---|---|---|---|---|
| 0x100 | ENGINE_STATUS | 10 ms / 0 ms | EngineRPM 0\|16 u ×0.25 rpm; Gear 16\|4 u | speed → gear (upshift 15/30/50/70/90 km/h) → RPM = wheel rpm × gear ratio × 3.9, min 800 |
| 0x120 | VEHICLE_SPEED | 20 ms / 1 ms | VehicleSpeed 0\|16 u ×0.01 km/h; LongAccel 16\|16 s ×0.001 m/s² | speed × 3.6; acceleration |
| 0x150 | ACCEL_PEDAL | 20 ms / 3 ms | AccelPedalPos 0\|8 u ×0.4 % | a ≥ 0: 15 % + 85 % × a / 2.6; else 0 |
| 0x180 | BRAKE_STATUS | 20 ms / 5 ms | BrakeActive 0\|1; BrakeDecel 8\|16 u ×0.001 m/s² | a < −0.3 m/s² → active, decel = −a |
| 0x200 | STEERING | 10 ms / 7 ms | SteeringAngle 0\|16 s ×0.1 deg; YawRate 16\|16 s ×0.01 deg/s | yaw rate = −Δheading/Δt; steer = atan(2.7 × yaw / v) × 15 |

Example: VehicleSpeed 48.27 km/h → raw 4827 = 0x12DB → payload bytes 0–1 `DB 12`.
Values outside a signal's DBC range are clamped, and the count is recorded in `metadata.json → model_stats`.
