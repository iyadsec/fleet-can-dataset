# FLEET-GUARD descriptor compatibility

## Authoritative definition

The 24-D behavioural descriptor is reproduced — not redesigned — from `iyadsec/fleet-can-ids @ 4f1ebf3`
(`src/features/feature_extractor.py`, `window_generator.py`, `dataset_loader.py`, `descriptor_generator.py`,
`configs/default.yaml`). The reproduction lives in `analysis/descriptor_schema.py`,
`analysis/windowing.py` and `analysis/descriptor_extractor.py` of `iyadsec/fleet-can-dataset` (reference
implementation, per-window Python loop). This release computes the same descriptor with a vectorised implementation
(`research/descriptors.py`) and verifies equivalence against the reference on real sessions.

| Item | Original rule | This release |
|---|---|---|
| Trace | one vehicle recording | one (session, vehicle) stream; windows never mix vehicles or sessions |
| Order | recording order | `timestamp_ns` (bus end-of-frame), ties by `frame_index` (none occur) |
| Window | 100 frames, overlap 50 → stride 50, start at frame 0, trailing partial window dropped | identical |
| Features | 24, fixed names and order (`DATA_DICTIONARY.md` §2) | identical names and order |
| CAN-ID entropy | −Σ p log2(p + 1e−12) over the window's ID counts | identical |
| Inter-arrival | mean / population std of `diff(t)` in seconds over the 99 gaps | identical (t = `timestamp_ns`/1e9) |
| DLC | nan-mean / nan-std | identical |
| Payload | bytes 0..7, NaN where index ≥ DLC, nan-mean / nan-std per position | identical |
| Missing values | `fillna(0.0)` at the descriptor stage | identical; count stored in `nan_filled_features` |
| Timestamp | none in the original | `descriptor_timestamp_ns` = timestamp of the window's last frame (earliest time the descriptor exists); `window_start_ns` = first frame |
| Window label | `_window_label`: majority of frame labels, tie → malicious | `label_majority_original` (separate file); `label_any_malicious` provided as documented alternative |

Host wall-clock capture time, configured attack/campaign times and generation time are never used.

## Verification

`research/descriptor_compat.py` runs the reference extractor and the reference labeller on the source observations of
one session per (scenario, attack family, fleet size, route group) cell and compares with the release:
window count, (vehicle, window index) keys, first/last frame, window start, descriptor timestamp, NaN-fill count, all
24 features (tolerance 1e−9 relative; bit-identity reported), malicious frame count, window class, attack IDs,
campaign IDs, first malicious time and the majority rule. Results: `validation/descriptor_compatibility.csv`,
summary in `reports/descriptor_methodology.md`. Differences, where any, are floating-point summation-order effects
of ≤ 1 ulp-scale (≈ 4e−16) in `can_id_entropy`.

## Storage

`data/descriptors/<sid>/descriptors.parquet` (+ `.csv.gz`): keys + 24 features only. Labels are in
`data/ground_truth/<sid>/window_labels.*` and join on (`vehicle_id`, `window_index`). Raw CAN data are not modified.
