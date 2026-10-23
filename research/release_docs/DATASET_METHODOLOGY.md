# Dataset methodology

## Purpose

This is a controlled synthetic benchmark for fleet-level detection of coordinated CAN attacks (FLEET-GUARD) and for
the adversarial robustness of fleet-level correlation (ROBUST-FLEET-GUARD). The design varies fleet size N, campaign
size K, attack family, timing pattern and hard negatives (independent attacks with matched timing) under fixed seeds.

## Source and versioning

| Version | Role | Status |
|---|---|---|
| fleetcan-sim-1.1 | earlier small-fleet baseline (N ≤ 10) | frozen, not part of this release |
| fleetcan-sim-scale-1.0 | 835 sessions; raw CAN + ground truth; frozen 2026-10-18 | frozen source (unchanged) |
| **fleetcan-sim-research-1.0** | derived research release (this) | detector-safe observations, separated ground truth and metadata, descriptors, validation |

The frozen matrix (`fleetcan-sim-scale-1.0-matrix-v1`, SHA-256 `9fc005de…e735`) defines 167 configurations, each run
with 5 seeded repetitions (seed = 920000 + 10·config_index + repetition). See `CAMPAIGN_DEFINITIONS.md`.

## Derivation principles

1. **No change to scientific data.** CAN values, timestamps, labels, attack and campaign definitions, membership and
   configuration are exactly as in the source.
2. **Only permitted transformations:**
   * a detector-safe column subset with standard names (`timestamp_ns` = `tx_end_ns`, `data` = `payload`);
   * lossless format conversion (Parquet zstd + delta encoding; gzip CSV);
   * join keys (`frame_index`);
   * derived, clearly prefixed observable timing (`derived_*`);
   * descriptors with the original definition;
   * documentation.
3. **Physical separation.** Observations, ground truth and metadata are kept in separate directory trees.
4. **Verification of every transformation.** Read-back equality, derivability of the dropped text columns,
   reference-implementation equivalence and checksums.
5. **Independence from detector results.** The data were not filtered, tuned or regenerated using any detection
   metric. No difficult scenario, weak attack or G0 session was removed.

## Observation model

* A detector sees, per vehicle bus: the bus end-of-frame time, the 11-bit CAN ID, the DLC and the payload.
* Everything else is evaluation or provenance information: generation time, arbitration timing, host capture
  clock, labels and configuration.
* Fleet-level methods may align vehicles by observable timestamps on the shared simulation clock. The shared clock
  is an idealisation of fleet time synchronisation.

## Evaluation guidance

* **Detector input.** Use `scripts/detector_adapter.py` (`research/adapter.py`) for every detector-side input.
* **Splits.** Split at session level, never at window level, because overlapping windows would leak. The M7 split
  (`detection/split.py`) is the reference.
* **Pre-registration.** Before any test result, fix the threshold protocol (FR-07), the window-label rule (FR-08),
  the G0 handling (FR-12) and the start-up window handling (FR-06).
* **Temporal claims.** Report them against observable onset (`derived_*`), within the resolution limits in
  `KNOWN_LIMITATIONS.md`.
