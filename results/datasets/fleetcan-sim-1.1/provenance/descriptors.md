# Behavioural descriptors (Milestone 4 analysis layer)

The analysis layer (`analysis/`) is separate from the generator. It reads raw observations and
produces FLEET-GUARD behavioural descriptors with observable timestamps. Ground truth is joined
only afterwards, into a separate evaluation file.

```
GENERATION (src/):  SUMO -> CAN -> attacks/campaigns -> bus model -> vCAN -> raw + ground truth
ANALYSIS (analysis/): raw/can_frames.csv (observable columns) -> 100-frame windows
                      -> 24 behavioural features + timestamps -> descriptors.csv
EVALUATION (after):  descriptors + ground truth -> descriptor_labels.csv, temporal_alignment.csv
```

## Source of the method (reproduced, not re-designed)

Recovered from `iyadsec/fleet-can-ids` @ `4f1ebf3` (2026-09-12), the OCSLab FLEET-GUARD pipeline:

| Element | Original location | Reproduced as |
|---|---|---|
| Windowing | `src/features/window_generator.py`, `configs/default.yaml` (`window_size: 100`, `overlap: 50`, `stride: 50`) | `analysis/windowing.py` |
| 24 features | `src/features/feature_extractor.py` (`BEHAVIOURAL_FEATURE_COLUMNS`, `extract_window_features`) | `analysis/descriptor_extractor.py::window_features` |
| Payload bytes | `src/data/dataset_loader.py` (`byte0..byte7`, bytes at index >= DLC = NaN) | `payload_bytes` |
| Descriptor | `src/features/descriptor_generator.py` (24 features + separate `anomaly_score`; `fillna(0.0)`) | `build_descriptors` (features only, `fillna(0.0)`) |

The repository also contains a different 30-feature set in `src/ctt/features.py` (cross-dataset
can-train-and-test study: adds min/max IAT, message rate, ID transition/repetition, payload change
rates, deviation features). It is **not** the primary FLEET-GUARD descriptor and is not used here.

## Windowing

* Trace = one `(session_id, vehicle_id)` frame stream ordered by observation time (stable sort).
* Windows: `for start in range(0, n - 100 + 1, 50)`: 100 frames, stride 50 (50 % overlap), first window
  starts at frame 0, the trailing partial window is dropped. Exactly the original rule.
* A window never spans two vehicles or two sessions (traces are separated before windowing; the
  validator re-checks every window's frames).
* Window count per trace = floor((n − 100) / 50) + 1.

## The 24 behavioural features (exact definitions)

| # | Feature | Definition | Unit | Calculation | Source field |
|---|---|---|---|---|---|
| 0 | frame_count | frames in window | frames | n (always 100 for full windows) | — |
| 1 | unique_can_id_count | distinct CAN IDs | IDs | \|unique(can_id)\| | can_id |
| 2 | can_id_entropy | Shannon entropy of IDs | bits | −Σ p_k log2(p_k + 1e−12) | can_id |
| 3 | most_common_can_id_ratio | share of most frequent ID | ratio | max count / n | can_id |
| 4 | mean_inter_arrival_time | mean gap | s | mean(diff(t)) over 99 gaps | observation time |
| 5 | std_inter_arrival_time | gap spread | s | std(diff(t)), ddof = 0 | observation time |
| 6 | mean_dlc | mean DLC | bytes | nanmean(dlc) | dlc |
| 7 | std_dlc | DLC spread | bytes | nanstd(dlc), ddof = 0 | dlc |
| 8–15 | byte_mean_i | mean of payload byte i over frames with DLC > i | 0–255 | nanmean(byte_i) | payload |
| 16–23 | byte_std_i | std of payload byte i over frames with DLC > i | 0–255 | nanstd(byte_i), ddof = 0 | payload |

**Why 24-D:** 8 scalar window statistics + 8 byte means + 8 byte standard deviations.
Values that are undefined (a byte position absent from every frame of the window) are NaN and
filled with 0.0, as the original descriptor stage does; the count is kept in `nan_filled_features`.

Not part of the original 24 (so not added): payload entropy, burstiness, message rate.
Burstiness (std/mean IAT) and message rate appear in the original only as optional graph-view
derivations; here they are reported in sanity tables as clearly marked derived quantities.

## Anomaly score relationship

The original descriptor is **24 behavioural features + 1 separate `anomaly_score`** (not 23 + score):
a per-vehicle Isolation Forest (200 trees) trained on benign windows using the 24 features
(`fillna(0)`), score = −decision_function, normalised to [0, 1] against the benign reference
distribution, then strong/weak evidence thresholds 0.80 / 0.55. The publication graph similarity
(`similarity_feature_view: full_descriptor`) uses the 24 features only; the score selects which windows
become descriptors. Milestone 4 produces the 24 features only; no Isolation Forest is trained.

## Timestamps (new capability; the original windows carry none)

* `window_start_time` / `window_end_time`: observation time of the first / last frame of the window.
* `descriptor_timestamp = window_end_time`: the earliest moment the descriptor can exist.
* Observation time (`time_basis`): `transmission` (default) = modelled end-of-frame bus time
  (`tx_end_ns`), what a bus logger would timestamp; `capture` = vCAN kernel receive time (includes
  host/VM jitter). Generation time, configured campaign/attack times and relative delays are never
  used: they are not observable.

## Anti-leakage controls

1. The extractor reads only `OBSERVATION_COLUMNS` (frame_uid, session_id, vehicle_id, can_id, dlc,
   payload, tx_end_ns, capture_time_ns) via `usecols`.
2. `build_descriptors` raises `LeakageError` if any `PROHIBITED_FIELDS` column is present (campaign_id,
   attack_id, attack_type, frame_label, label, coordinated, relative_delay*, campaign/vehicle attack
   start/end, scenario_id, frame_source, message_name, sim_time_ns, timestamp, tx_start_ns,
   queue_delay_us, ...). One test per field.
3. `descriptors.csv` contains identifiers, timestamps and the 24 features only. Ground truth goes to
   `descriptor_labels.csv` (window_class benign_only / malicious_only / mixed, malicious_frame_count and
   ratio, all attack_ids / campaign_ids / attack_types in the window) produced afterwards.

## Temporal alignment

Detector side: `delta_t(i, j) = |descriptor_timestamp_i − descriptor_timestamp_j|` (observable only).
Evaluation side (`temporal_alignment.csv`): per campaign member, delays relative to the earliest
member for (a) configured start, (b) first malicious frame (bus time), (c) first descriptor containing
>= 1 malicious frame, (d) first descriptor with >= 50 % malicious frames; plus each member's lag from
configured start to (c) and (d).

## FLEET-GUARD compatibility

| Original FLEET-GUARD element | Status with the new dataset |
|---|---|
| Input: per-frame timestamp, CAN ID, DLC, payload | Supplied (bus time, captured ID/DLC/payload) |
| 100-frame window, stride 50 | Reproduced exactly |
| 24 behavioural features | Reproduced exactly (same definitions, order, NaN handling) |
| Isolation Forest anomaly score | Not produced in Milestone 4 (by design); interface defined (score is separate from the 24) |
| Descriptor (24 + score) | 24-D part compatible; score column to be added when the local IDS is trained |
| Fleet graph / GraphSAGE / DBSCAN input | Not implemented (Milestone 4 scope) |
| Descriptor timestamp | **New** (original has none) |
| Campaign ground truth (membership, delays, overlaps) | **New** (original used scenario assignment on OCSLab traces) |
| Multi-vehicle synchronized traffic | **New** (original: independent recordings per vehicle model) |

## Vehicle heterogeneity

See `config/m4/heterogeneity/vehicles_heterogeneous_example.yaml` (profiles A, B, C) and the
Milestone 4 report. The descriptor stays 24-D for every profile: all 24 features are defined over
generic frame fields (ID, DLC, bytes, time), independent of the DBC.
