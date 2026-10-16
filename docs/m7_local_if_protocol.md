# Milestone 7 — local (vehicle-level) Isolation Forest baseline: pre-registered protocol

Status: **recorded before any test-split result was computed** (committed ahead of the first run).
Dataset: `fleetcan-sim-1.1` (frozen, 610 sessions, read-only; never written by M7).
Outputs: `results/m7_local_if/` (outside the dataset). Code: `detection/`, `scripts/m7_run.py`.

## 1. Authoritative original implementation (audited)

Source: `iyadsec/fleet-can-ids` @ `4f1ebf3` (= current upstream `main`; same commit the 24-D
descriptor was reproduced from in M4). The repository contains three Isolation-Forest paths:

| Path | Files | Role |
|---|---|---|
| **P1 publication vehicle-level evaluator** (FPR-controlled) | `run_vehicle_level_evaluation.py`, `src/evaluation/vehicle_level_evaluation.py::run_vehicle_level_evaluation` | corrected paper Table I (local IDS metrics) |
| **P2 fleet pipeline** (evidence levels) | `experiments/04_train_vehicle_ids.py`, `src/models/vehicle_ids.py::generate_vehicle_anomaly_predictions`, `src/features/descriptor_generator.py` | attaches `anomaly_score` + strong/weak evidence to descriptors for the fleet layer |
| P3 cross-dataset onboarding | `src/ctt/local_detector.py` | other dataset family (StandardScaler, extra deviation features, 90th/97.5th-percentile thresholds); **not** the FLEET-GUARD baseline |

Common to P1/P2 (`src/models/vehicle_ids.py`):

| Item | Original code |
|---|---|
| Constructor | `IsolationForest(n_estimators=200, contamination="auto", random_state=42, n_jobs=-1)`; all else sklearn defaults (`max_samples="auto"` = min(256, n), `max_features=1.0`, `bootstrap=False`) |
| Granularity | one model **per vehicle model / OEM** (`groupby("vehicle_model")`: Chevrolet, Hyundai, Kia) |
| Input | `BEHAVIOURAL_FEATURE_COLUMNS` (the 24 features), `fillna(0.0)`, `float32`; **no scaling** |
| Training data | benign windows only (`benign_training_mask`: label == 0); P1: benign windows of the 70 % training partition |
| Raw score | `-decision_function(X)` → **higher = more anomalous** |
| Normalisation to [0,1] | `_normalised_anomaly_percentile`: `searchsorted(sort(benign_train_raw), raw, side="right") / n_benign_train`, clipped to [0,1] — the empirical CDF of the benign **training** raw scores (reference = the training windows themselves) |
| Window label | `_window_label`: majority of frame labels, tie → malicious (= `malicious_frame_ratio >= 0.5`) |
| Attaching the score (P2) | `descriptor_generator`: per (window_id, vehicle_model) `anomaly_score` = max over IDS outputs (one IF → its score); descriptors are emitted only for strong/weak evidence |

P1 only (detection metrics):

| Item | Original code |
|---|---|
| Split | `train_test_split(stratify=label, random_state=42)` **per vehicle, at window (row) level**: 70 % train / 15 % validation / 15 % test |
| Threshold | per vehicle on **validation only**: candidates = unique 300 quantiles (0.001…0.999) of validation scores; among candidates with validation FPR ≤ 5 % pick the **highest recall**; fallback FPR ≤ 10 %, then 0.99; predict `score >= t` |
| Metrics | PR-AUC = `average_precision_score`, ROC-AUC, precision, recall, F1, FPR; pooled = per-vehicle predictions concatenated |
| Reported thresholds | Chevrolet 0.9571, Hyundai 0.9605, Kia 0.9504 (pooled reference 0.9558) — **derived**, not 0.80 |

P2 only (evidence levels): `strong_threshold = 0.80`, `weak_threshold = 0.55` are **fixed constants**
(`configs/fleet_ids.yaml::vehicle_ids`, CLI defaults); `local_alert = s >= 0.80`,
`weak_signal = 0.55 <= s < 0.80`, `evidence_level ∈ {strong_local_anomaly, weak_suspicious_signal, normal}`.
They are not calibrated; with the percentile normalisation they mean "more anomalous than 80 % / 55 % of
the benign training windows". (An archived balanced run used per-vehicle strong thresholds 0.85 / 0.70;
not in the code.)

### Discrepancies with the previous research record

1. "benign-only, ~200 trees, score in [0,1]" — **confirmed** (exactly 200; normalisation = benign-train empirical CDF).
2. "θ_strong = 0.80, θ_weak = 0.55" — confirmed as **fixed** evidence constants of the fleet pipeline (P2), **not** derived;
   they are **not** the detection thresholds of the corrected paper evaluation (P1 thresholds ≈ 0.95).
3. "validation FPR ≤ 5 %, highest recall" — **confirmed** for P1, per vehicle, validation only (+ 10 % fallback).
4. "PR-AUC ≈ 0.963, P ≈ 0.900, R ≈ 0.838, F1 ≈ 0.868" — these are the **superseded** old manuscript Table I
   (fixed / F1-oriented threshold, pooled FPR ≈ 37 %), documented in the original `results/per_vehicle_validation_report.md`.
   The original's **corrected FPR-controlled** pooled result (`results/vehicle_level_threshold_comparison.csv`, FPR≤5 % row):
   ROC-AUC 0.7855, PR-AUC 0.9273, precision 0.9724, recall 0.4597, F1 0.6243, FPR 0.0402.
5. Original split is random **window-level** stratified — overlapping windows of the same trace fall into train and
   test. M7 forbids this (leakage); M7 splits at session level (deviation, required).
6. Original benign training windows include benign-majority windows from attack captures (label 0 can contain up to
   49 % attack frames). M7 trains on S0 benign sessions only (deviation, required by the M7 specification; the
   original does not *require* attack captures — it is a property of the OCSLab file layout).

## 2. M7 design (fixed before evaluation)

**Granularity.** One benign-only IF per synthetic profile: IF-A, IF-B, IF-C. Profiles play the role of the original
per-OEM models (different DBC / IDs / periods / payload layouts). Profile is used only to route a descriptor to its
model; it is never a feature. No global model.

**Input.** Exactly the 24 frozen features in frozen order, parsed to float, `fillna(0)`, `float32`, no scaling
(as original). Enforced by `detection.features.model_matrix` (allow-list; any prohibited/extra column → `LeakageError`).

**Split (session level, seed 42).** Unit = generated session; all windows of a session share its split.
Per configuration (5 repetitions) the repetition order is permuted with `SeedSequence([42, crc32(config_id)])`:
* S0 configurations (3: N = 2/5/10): 3 train / 1 validation / 1 test (closest to 70/15/15 with 5 sessions);
* attack configurations (119): 0 train / 2 validation / 3 test (attack sessions are never used for fitting;
  the original also discarded attack windows of its training partition).
Totals: train 9 sessions (S0 only), validation 3 S0 + 238 attack = 241, test 3 S0 + 357 attack = 360.
Stratification by configuration covers fleet size, scenario, attack, campaign size and pattern; profile
composition per split is reported.

**Training.** IF-p is fitted on all descriptors of profile-p vehicles in the 9 training sessions (S0; all windows
are `benign_only` — asserted). Hyper-parameters exactly as original (above), `random_state=42` for every model.

**Score.** raw = −decision_function (higher = more anomalous). `anomaly_score` = original percentile normalisation
against the model's own benign training raw scores (no validation/test statistics). Scores for all 981,088
descriptors (train/validation/test) are written to `descriptor_scores.parquet` with the split; labels are joined
only inside evaluation.

**Thresholds (validation only, per profile, original P1 procedure).**
* Detection threshold θ_det,p: FPR ≤ 5 % → highest recall (fallback 10 %, then 0.99) on profile-p validation
  descriptors, labels = **Evaluation A** (positive: `malicious_frame_count > 0`; negative: `benign_only`).
  Deviation from the original label rule (majority) recorded: under majority labels Malfunction has no positive
  window at all and minority-attack windows would count as false alarms. The θ under majority labels
  (Evaluation B) and a pooled θ are also computed on validation and reported as sensitivity.
* Evidence bands — **primary (original P2 constants)**: low s < 0.55 ≤ weak < 0.80 ≤ strong.
  **Secondary (FPR-calibrated, original P1 caps)**: weak ≥ θ(FPR ≤ 10 %), strong ≥ θ(FPR ≤ 5 %) = θ_det, per profile.
  Neither band uses attack intensity or test data.

**Evaluation (test sessions only).**
* Evaluation A: positive = attack-containing (`malicious_frame_count > 0`), negative = `benign_only`.
* Evaluation B: positive = majority malicious (`malicious_frame_ratio >= 0.5`, the original label), negative = all other
  windows (original semantics). Families without positives (expected: Malfunction, max ratio 0.28) are reported as such.
* Metrics: PR-AUC (average precision), ROC-AUC, precision, recall (= TPR), F1, FPR, counts; pooled = per-profile
  predictions concatenated (original). Also reported: the fixed-0.80 rule (original P2 `local_alert`).
* Per profile; per attack family (test sessions of that family: their attack-containing windows vs their benign_only
  windows); per family × profile; per scenario (S0, S1, S2, S3, S3-mixed, S3-probe), split into attacked vs
  non-attacked vehicles; S3 campaign size 2/5/10; temporal pattern 0 / 0.5 / 2 / 10 s / partial / 0.1 s probe —
  all per vehicle, no cross-vehicle aggregation.
* False positives: test `benign_only` descriptors in weak/strong bands, by profile / scenario / vehicle / session,
  and against: time since switch-on, window position in the trace, vehicle speed/acceleration change in the window,
  the most deviating feature (|z| vs the profile's training statistics), distance to the vehicle's own attack.

**Ablation (after the primary run).** Per-profile `StandardScaler` fitted on the training descriptors only, then the
identical IF / normalisation / thresholds. Reported next to, never instead of, the primary result.

**Reproducibility.** The full pipeline is run twice into separate directories; split, configuration, scores,
thresholds, predictions and metrics are compared (maximum |score difference| reported).

**Prohibited (enforced or checked):** training on validation/test sessions, threshold selection on test data,
row-level random splitting, labels / attack / campaign / scenario / timing / profile / vehicle identity as features,
any write into `data/`.
