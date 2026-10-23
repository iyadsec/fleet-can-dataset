# Data dictionary — fleetcan-sim-research-1.0

Every session `<sid>` (835, e.g. `FCS10_N050_S3_replay_K010_partial_R3`) has one directory in each of the five
data areas. **Only `raw_parquet` / `raw_csv` (and `descriptors`) are detector-facing.** Ground truth and metadata
are physically separate and must never become predictive features.

| Area | Path | Detector-facing | Formats |
|---|---|---|---|
| Observations | `data/raw_parquet/<sid>/can_frames.parquet`, `data/raw_csv/<sid>/can_frames.csv.gz` | **yes** | Parquet (zstd), gzip CSV |
| Descriptors | `data/descriptors/<sid>/descriptors.parquet` / `.csv.gz` | **yes** (24 features only) | Parquet, gzip CSV |
| Ground truth | `data/ground_truth/<sid>/*` | **no** — evaluation only | Parquet + CSV |
| Metadata | `data/metadata/<sid>/*` | **no** — bookkeeping / provenance | JSON, CSV, Parquet |

Session identity is carried by the directory name only. No detector-facing file contains `session_id`,
`scenario_id`, `frame_uid`, labels, campaign identifiers or configuration.

## 1. Observations — `can_frames` (detector-safe)

| Column | Type | Role | Definition |
|---|---|---|---|
| `frame_index` | int64 | ordering / join key — **not a feature** | 0-based row ordinal in the source `raw/<sid>/can_frames.parquet`. Equals the numeric suffix of the source `frame_uid` (`<sid>-F<9 digits>`, verified for every frame). Used to join ground truth and to break timestamp ties deterministically. |
| `timestamp_ns` | int64 | feature input (relative differences only) | Observable bus time of the frame: source `tx_end_ns`, the modelled instant the frame is complete on the logical CAN bus (what a bus logger time-stamps), on the shared SUMO timeline, in ns. |
| `vehicle_id` | string | grouping key — **not a feature** | Vehicle (one logical CAN bus per vehicle). |
| `can_id` | string | feature input | 11-bit identifier exactly as in the source: `0x` + 3 upper-case hex digits. |
| `dlc` | int8 | feature input | Data length code 0..8. |
| `data` | string | feature input | Payload exactly as in the source: `2·dlc` upper-case hex digits (empty string when `dlc = 0`). |

Row order = source row order = global non-decreasing `timestamp_ns` (verified). Within one vehicle `timestamp_ns`
is strictly increasing (verified: 0 ties in all 835 sessions), so the per-vehicle observable order is unique; the
adapter still sorts by (`vehicle_id`, `timestamp_ns`, `frame_index`).

Values are not transformed: `can_id`, `dlc` and `data` are the source text/values; `timestamp_ns` is the source
integer. Parquet and CSV contain the same table (read-back equality verified per session).

## 2. Descriptors — `descriptors` (original FLEET-GUARD 24-D, per vehicle, 100-frame windows, stride 50)

Key columns (not features): `vehicle_id`, `window_index` (0-based per vehicle), `first_frame_index`,
`last_frame_index`, `window_start_ns` (timestamp of first frame), `window_end_ns` (timestamp of last frame),
`descriptor_timestamp_ns` (= `window_end_ns`, the earliest moment the descriptor exists), `nan_filled_features`
(number of features that were NaN and filled with 0.0, as in the original).

Features (exact names and order, `analysis/descriptor_schema.py`, reproduced from `iyadsec/fleet-can-ids @ 4f1ebf3`):

| # | Feature | Definition (over the 100 frames of the window) |
|---|---|---|
| 0 | `frame_count` | 100 |
| 1 | `unique_can_id_count` | number of distinct CAN IDs |
| 2 | `can_id_entropy` | −Σ p_k log2(p_k + 1e−12) |
| 3 | `most_common_can_id_ratio` | max_k count_k / 100 |
| 4 | `mean_inter_arrival_time` | mean of the 99 gaps of `timestamp_ns`/1e9 (s) |
| 5 | `std_inter_arrival_time` | population std of the gaps (s) |
| 6 | `mean_dlc` | mean DLC |
| 7 | `std_dlc` | population std of DLC |
| 8–15 | `byte_mean_0` … `byte_mean_7` | nan-mean of payload byte i over frames with DLC > i |
| 16–23 | `byte_std_0` … `byte_std_7` | nan-population-std of payload byte i |

Missing payload bytes (index ≥ DLC) are NaN before the statistics; a feature that is NaN for the whole window
(e.g. byte 7 when every frame has DLC ≤ 7) is filled with 0.0 (original `fillna(0.0)`).

## 3. Ground truth (evaluation only)

| File | Content |
|---|---|
| `frame_labels.parquet` / `.csv.gz` | one row per frame: `frame_index`, `frame_label` (benign/malicious), `attack_type`, `campaign_id`, `attack_id`, `frame_source` (benign_generator / attack_injection / attack_modification), `message_name`. Original values; `frame_uid` and `session_id` are omitted because they are derivable (`<sid>-F{frame_index:09d}`). |
| `attacks.parquet` / `.csv` | original attack instances (configured start/end, rates, generated/transmitted/dropped/captured counts, seed, parameters) |
| `campaigns.parquet` / `.csv` | original campaigns (coordinated flag, delay pattern, targets, configured start/end, overlap) |
| `campaign_membership.parquet` / `.csv` | original vehicle membership with configured per-vehicle start/end and relative delay |
| `campaign_overlap.parquet` / `.csv` | original pairwise configured overlap |
| `attack_frames.parquet` / `.csv.gz` | original per-malicious-frame details (+ `frame_index`) |
| `replay_map.parquet` / `.csv.gz` | original replay links (+ `replay_frame_index`, `original_frame_index`) |
| `dropped_frames.parquet` / `.csv` | original dropped-frame records (empty: no drops occurred) |
| `attack_instances.parquet` / `.csv` | **derived**: original attack fields + observable timing. Every derived column starts with `derived_` (first/last malicious frame, onset/end delays, first window with ≥1/5/50 attack frames, injection rate, bus effects). Configured values are kept unchanged next to them. |
| `window_labels.parquet` / `.csv.gz` | **derived**, one row per descriptor window: `malicious_frame_count`, `malicious_frame_ratio`, `label_majority_original` (original FLEET-GUARD rule: ratio ≥ 0.5, tie → malicious), `label_any_malicious` (≥ 1 malicious frame; M7 "Evaluation A"), `window_class`, `attack_ids`, `campaign_ids`, `attack_types`, `first_malicious_timestamp_ns`. |

## 4. Metadata (never a feature)

| File | Content |
|---|---|
| `session.json` | scenario, fleet size N, campaign size K, attack family, temporal pattern, configuration, repetition, seed, route group (G0/P1/P2), route source, member-selection rule, profile composition, generator version/commit, matrix ID/SHA, SUMO version, route seed/SHA, source file list |
| `vehicles.csv` | per vehicle: profile, channel, ECU `switch_on_ns`, first SUMO state time, `ecu_activation_ns`, route loop/start edge/departure position, mean/max speed |
| `frame_bus_timing.parquet` | non-observable per-frame fields kept for lossless reconstruction: `sim_time_ns` (scheduled generation time), `tx_start_ns`, `frame_bits`, `capture_time_ns` (host wall clock — leakage-prone), `channel` |
| `source/metadata.json`, `source/bus_stats.json`, `source/config_snapshot/`, `source/generator_validation_report.json` | verbatim copies of the source session metadata |

## 5. Prohibited detector inputs

`scenario_id`, `scenario`, `session_id`, `frame_uid`, `capture_time_ns`, `capture_offset_us`, `sim_time_ns`,
`tx_start_ns`, `queue_delay_us`, `frame_bits`, `channel`, every label / attack / campaign / membership / coordinated
field, configured attack or campaign times, configuration identifiers, repetition, seeds, route group / route
metadata, profile, fleet size, ECU switch-on — and any column not on the allow-list. Vehicle and session identity may
be used for grouping, splitting and evaluation bookkeeping only. Enforcement: `scripts/detector_adapter.py`
(`research/adapter.py`), tests `tests/test_metadata_leakage.py`.

## 6. Lossless reconstruction of the source `can_frames`

source column ← release: `frame_uid` = `<sid>-F{frame_index:09d}`; `session_id` = directory; `scenario_id` =
`session.json` scenario prefix (S0/S1/S2/S3); `tx_end_ns` = `timestamp_ns`; `vehicle_id`, `can_id`, `dlc`,
`payload` (= `data`) unchanged; `sim_time_ns`, `tx_start_ns`, `frame_bits`, `capture_time_ns`, `channel` from
`frame_bus_timing.parquet`; `timestamp` = `sim_time_ns`/1e9 (9 decimals), `transmission_timestamp` = `tx_end_ns`/1e9
(9 decimals), `queue_delay_us` = (`tx_start_ns` − `sim_time_ns`)/1000 (3 decimals), `capture_offset_us` = "" (all
sessions). Each derivation was verified on every frame (`validation/source_schema_validation.csv`).
