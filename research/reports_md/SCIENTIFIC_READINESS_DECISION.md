# Scientific readiness decision — fleetcan-sim-research-1.0

Date: 2026-10-23. Source: fleetcan-sim-scale-1.0 (frozen, unchanged).

## Decision: **RESEARCH DATASET CONDITIONALLY READY**

All integrity, completeness, structural, leakage, labelling, temporal and reproducibility checks pass on all 835
sessions, and **no genuine data defect (category A) was found**. Four methodological decisions must be approved
before the FLEET-GUARD / ROBUST-FLEET-GUARD evaluation begins. None of them requires a change to the data.

## Gate

| Requirement | Acceptance condition | Status | Evidence |
|---|---|---|---|
| Source integrity | Verified | **PASS**: 26,740/26,740 files; SHA256SUMS, manifest and matrix anchors match FREEZE.json | `validation/source_integrity.csv` |
| 835 sessions | Accounted for | **PASS**: 835 unique, 167 configurations × 5 repetitions | `validation/source_manifest_validation.csv` |
| 444,333,222 frames | Accounted for | **PASS**: same count in observations, frame labels and bus timing | `validation/output_integrity.csv` |
| Detector-safe observation schema | Verified | **PASS** (835/835) | `validation/P1_metadata_leakage.csv` |
| Metadata-leakage prevention | Automated tests pass | **PASS** (22/22 leakage tests, 41/41 overall) | `validation/test_results.csv` |
| Ground-truth separation | Verified | **PASS** | `validation/P1_metadata_leakage.csv` |
| Benign traffic characterisation | Complete | **PASS** (48,150 traces) | `results/benign_traffic_statistics.csv` |
| Benign-only FPR sanity check | Completed and documented | **PASS**: pooled FPR 0.208 vs 0.20 by construction; P1 threshold not computed (needs approval) | `reports/P1_benign_behavior.md` |
| Observable timestamps | Verified | **PASS** | `validation/P1_temporal_consistency.csv` |
| Descriptor timestamps | Verified | **PASS** (8,814,858 windows) | same |
| Window labelling | Consistent with original methodology | **PASS**: original majority rule reproduced exactly; rule choice open | `validation/descriptor_compatibility.csv` |
| Replay source relationships | Verified | **PASS** (3,065/3,065) | `validation/source_replay_validation.csv` |
| Replay onset discrepancies | Explained or escalated | **PASS**: 3,055 expected, 10 explained by ECU activation | `validation/P2_replay_validation.csv` |
| G0 route bias | Assessed | **PASS** (no route effect) | `reports/P2_G0_bias.md` |
| Vehicle-identity bias | Assessed | **PASS** (assessed): none in P1/P2; deterministic in G0 → separate evaluation recommended | `results/vehicle_identity_bias.csv` |
| Campaign timing | Validated | **PASS** (575 coordinated + 1,320 independent) | `results/campaign_timing.csv` |
| Dataset-wide statistics | Complete | **PASS** | `results/` |
| Full CSV exports | Complete | **PASS**: 835 sessions as gzip CSV (owner-approved format); read-back equal | `data/raw_csv/` |
| Full Parquet exports | Complete | **PASS** (835 sessions, read-back equal) | `data/raw_parquet/` |
| HTML dashboard | Generated and accessible | **PASS** | `reports/html/index.html` |
| PDF validation report | Generated | **PASS** | `reports/FLEETCAN_SIM_Research_Dataset_Validation.pdf` |
| Reproducibility documentation | Complete | **PASS** | `documentation/REPRODUCIBILITY.md` |
| Output checksums | Verified | **PASS** | `SHA256SUMS`, `validation/output_integrity.csv` |
| Frozen source unchanged | Verified | **PASS** (re-hashed after export) | `validation/source_integrity_after_export.csv` |
| Critical unresolved data defects | None | **PASS** (0 category A issues) | `validation/issue_register.csv` |

## Conditions (approval required; `validation/unresolved_issues.csv`)

1. **FR-07 — threshold protocol.** The original detection threshold ("validation FPR ≤ 5 %, highest recall") needs
   labelled attack windows in a validation partition. Approve a leakage-free, pre-registered protocol for this, for
   example the M7 session-level split with validation sessions only and thresholds frozen before any test result.
2. **FR-08 — window-label rule.** Under the original majority rule no malfunction window is ever positive, and
   replay windows are positive only through the tie rule. Decide whether the main evaluation uses
   `label_majority_original`, `label_any_malicious`, or both (as M7 did), and report the choice.
3. **FR-12 — G0 handling.** In G0, vehicle identity encodes the attack role (first-K members, 2-digit IDs).
   Recommendation: evaluate G0 separately for campaign-level metrics; pooling is fine for benign and local analyses.
4. **FR-06 — start-up windows.** The first windows of every trace (ECU switch-on) are almost always locally anomalous
   (98.6 % strong). Decide whether to exclude, stratify, or keep and report them.

## Not blocking, documented

* **FR-03:** absolute time and the warm-up are potential shortcuts.
* **FR-04:** benign variability is low.
* **FR-05:** benign FPR is fixed by construction.
* **FR-09/FR-10:** replay onset differences are explained.
* **FR-11:** temporal resolution limits.
* **FR-13–FR-16:** route source, attack semantics, idealised bus, generation provenance.

**Scope.** This dataset is synthetic. It contains no production ECU captures, the logical CAN bus is not a physical
bus, and real-world generalisation has not been established. Readiness here means readiness for controlled
experiments on this synthetic benchmark. It is not a claim about real vehicles.
