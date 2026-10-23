# fleetcan-sim-research-1.0

A synthetic multi-vehicle CAN dataset with coordinated attack campaigns, prepared for the FLEET-GUARD and
ROBUST-FLEET-GUARD experiments. It is a **derived release** of the frozen `fleetcan-sim-scale-1.0`: no CAN frame,
timestamp, label or configuration was changed.

**Synthetic data.** SUMO mobility, synthetic CAN generation (profiles A/B/C), a logical CAN bus model and SocketCAN
vCAN transport. No production ECU captures. SUMO does not generate CAN traffic itself. The logical bus is not a
physical bus. Real-world generalisation has not been established.

| Property | Value |
|---|---|
| Sessions / configurations / repetitions | 835 / 167 / 5 |
| CAN frames | 444,333,222 (benign 395,777,880; malicious 48,555,342) |
| Vehicle traces | 48,150 (profiles A 16,435 / B 16,115 / C 15,600) |
| Fleet sizes | 10 (G0 bridge, 25 sessions), 20 (245), 50 (270), 100 (295) |
| Scenarios | S0 20, S1 60, S2 180, S3 530, S3-mixed 30, S3-probe 15 |
| Attack instances | 12,180 (flooding 3,270, fuzzy 3,090, malfunction 2,755, replay 3,065) |
| Campaigns | 1,895 (575 coordinated, 1,320 independent) |
| Descriptor windows (24-D, 100/50) | 8,814,858 |
| Release size / files | {{TOTAL_SIZE}} / {{FILES}} files |
| Readiness | **RESEARCH DATASET CONDITIONALLY READY** — see `reports/SCIENTIFIC_READINESS_DECISION.md` |

## Layout

```
README.md  MANIFEST.csv  SHA256SUMS
data/raw_parquet/<sid>/can_frames.parquet     detector-safe observations (frame_index, timestamp_ns, vehicle_id, can_id, dlc, data)
data/raw_csv/<sid>/can_frames.csv.gz          the same table as gzip CSV
data/ground_truth/<sid>/                      labels, attacks, campaigns, membership, replay map, derived timing, window labels
data/metadata/<sid>/                          session.json, vehicles.csv, frame_bus_timing.parquet, source/ (verbatim copies)
data/descriptors/<sid>/descriptors.parquet    original FLEET-GUARD 24-D descriptors (+ .csv.gz); no labels
results/                                      dataset-wide statistics (complete, all sessions)
validation/                                   validation tables, issue register, integrity
reports/html/index.html                       searchable local dashboard
reports/FLEETCAN_SIM_Research_Dataset_Validation.pdf
reports/*.md                                  P1/P2 readiness reports, descriptor methodology, readiness decision
documentation/                                data dictionary, methodology, architecture, profiles, attacks, campaigns,
                                              ground truth, validation methods, descriptor compatibility, limitations,
                                              reproducibility, citation
scripts/                                      export, statistics, validation, reports, integrity, detector adapter
```

## Quick start

```python
import pandas as pd
sid = "FCS10_N050_S3_replay_K010_partial_R3"
obs = pd.read_parquet(f"data/raw_parquet/{sid}/can_frames.parquet")        # detector input
desc = pd.read_parquet(f"data/descriptors/{sid}/descriptors.parquet")       # 24 features + window keys
lab = pd.read_parquet(f"data/ground_truth/{sid}/window_labels.parquet")     # evaluation only
ev = desc.merge(lab, on=["vehicle_id", "window_index"])                     # join only inside evaluation
```

For every detector-side input, use `scripts/detector_adapter.py`. It enforces the allow-list. Never use ground
truth or metadata as features, and see `documentation/DATA_DICTIONARY.md` §5 for the prohibited fields.

## Integrity

```bash
sha256sum -c SHA256SUMS
```

Source dataset: `fleetcan-sim-scale-1.0`, SHA256SUMS `5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70`.
Citation: `documentation/CITATION.md`.
