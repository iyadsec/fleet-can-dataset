# P1 — Metadata-leakage prevention

**Result: PASS.** All 835 sessions expose leakage-safe observation files. Ground truth and metadata are physically
separate. 22/22 automated leakage tests pass (41/41 including the temporal tests). No prohibited field enters detector
features.

## Finding addressed

The source raw frames (`raw/<sid>/can_frames`) contain `scenario_id`, `session_id` and `frame_uid`, which encode
N, scenario, family, K and timing, and `capture_time_ns` (host wall clock, correlated with generation order).
Read naively, a detector could learn session identity instead of CAN behaviour (issue FR-01).

## Safeguards

| Layer | Implementation | Guarantee |
|---|---|---|
| Physical separation | `data/raw_parquet`, `data/raw_csv` hold only `frame_index, timestamp_ns, vehicle_id, can_id, dlc, data`. Labels are in `data/ground_truth`; configuration and host/bus timing are in `data/metadata`. | 835/835 sessions verified (`validation/P1_metadata_leakage.csv`) |
| Observation allow-list | `research/adapter.py::sanitize_observations` | Unknown or prohibited columns raise `LeakageError` (strict) or are dropped (exclude) |
| Loader | `adapter.load_observations` | Opens only `data/raw_parquet/<sid>/can_frames.parquet` and refuses any path in `ground_truth`/`metadata`; session identity comes from the directory name, never from a column |
| Descriptor | `adapter.descriptors` → original 24-D | Uses only `timestamp_ns` differences, `can_id`, `dlc` and `data` |
| Model input (IF, clustering, GraphSAGE node features) | `adapter.model_input` → `detection.features.model_matrix` | Exactly the 24 frozen features in frozen order; any extra column (profile, fleet size, labels, identity) raises an error |
| Graph construction / campaign logic | `adapter.graph_inputs`, `adapter.temporal_edges` | Only vehicle key, window index, descriptor timestamp and the 24 features; campaign, label and attack columns raise an error |

**Grouping, not features.** Vehicle identity and session identity may be used for grouping, splitting and evaluation
bookkeeping. Profile may be used to *route* a window to its per-profile model, as the original per-OEM models do.
None of them may be a numeric or categorical feature.

## Automated tests (`tests/test_metadata_leakage.py`)

| Test | What it proves |
|---|---|
| `test_adapter_rejects_each_prohibited_field` (×14) | `scenario_id`, `session_id`, `frame_uid`, `capture_time_ns`, `frame_label`, `attack_id`, `campaign_id`, `coordinated`, `attack_type`, `configured_start_ns`, `configuration_id`, `route_group`, `profile` and `fleet_size` are each rejected in strict mode and removed in exclude mode |
| `test_unknown_fields_rejected_or_excluded` | Unknown columns are rejected, not silently used |
| `test_descriptors_invariant_to_prohibited_metadata` | Descriptors are bit-identical after random prohibited metadata is injected and changed three times |
| `test_model_input_is_exactly_24_features` | The model matrix is 24 × float32; profile, fleet size, labels, campaign, session and route group are rejected; renaming vehicles leaves the matrix unchanged |
| `test_graph_construction_cannot_access_campaign_membership` | Campaign, coordinated, attack, window-class and count columns are rejected by the graph-input guard |
| `test_loader_only_opens_raw_parquet` | The loader refuses ground-truth paths |
| `test_release_observation_files_contain_only_allowed_columns` | Every Parquet and CSV observation file has exactly the allow-listed header (835 sessions) |
| `test_release_descriptors_contain_no_labels_or_metadata` | Every descriptor file holds keys plus the 24 features in frozen order (835 sessions) |
| `test_release_ground_truth_and_metadata_physically_separate` | Observation directories contain only the frame file; ground truth and metadata exist separately |

Results: `validation/test_results.csv`, `validation/P1_metadata_leakage.csv`.

## Remaining risk

* Experiment code that bypasses the adapter and reads metadata or ground truth directly is not protected.
* Absolute `timestamp_ns` (attacks start at ≥ 10 s) is observable. The 24-D descriptor uses only intra-window
  differences, and `descriptor_timestamp_ns` is meant for cross-vehicle alignment only. A detector that adds
  absolute time or time since start as a feature would exploit the fixed warm-up (FR-03).
