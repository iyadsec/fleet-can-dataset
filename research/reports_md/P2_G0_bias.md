# P2 — G0 route-source and vehicle-identity bias

**Result: assessed.**
* G0's route source does not change benign CAN traffic measurably.
* G0's attack assignment is fully determined by vehicle identity: members are always `vehicle_01` … `vehicle_05`,
  the vehicle IDs have 2 digits, and the member rule is first-K.
* **Recommendation: evaluate G0 separately for fleet-level campaign metrics. It may be pooled for benign-traffic and
  local, per-window analyses.** This is a methodological decision that needs approval (FR-12).
* No G0 session was deleted, and no route or assignment was changed.

## What G0 is

G0 is the route-source bridge: 5 configurations and 25 sessions, all with N = 10. Four configurations are S3 with
K = 5 (one per family; simultaneous or 0.5 s) and one is S0. It uses the baseline fleet file and the first-K member
rule, so it can be compared with fleetcan-sim-1.1, but with generated `grid_loops_v1` routes. P1/P2 (810 sessions,
N = 20/50/100) use seeded, profile-balanced member selection and 3-digit vehicle IDs.

## 1. Route-source effect on benign traffic (`results/G0_route_bias_statistics.csv`)

Non-attacked vehicles were compared per profile, so profile composition cannot confound the result:
* G0 (N = 10; 67 / 46 / 37 vehicles of A / B / C) against N = 20 (1,049 / 1,061 / 850);
* also against all P1/P2.

| Metric (per profile) | Max \|SMD\| over A/B/C | Min Mann–Whitney p |
|---|---|---|
| benign rate, ID entropy, inter-arrival, byte variability, DLC, speed, ECU switch-on, bus load, window span | 0.24 | 0.12 |
| 24 descriptor features of benign windows (G0 vs all P1/P2) | 0.13 | — |

* Every per-profile difference is small (|SMD| < 0.25) and non-significant.
* Pooled ("all profiles") differences, such as a benign rate of 326.1 vs 323.3 frames/s, disappear within each
  profile. They come from G0's profile mix.
* Fleet size is confounded with G0 (N = 10 exists only in G0), so comparisons stratify by N.
* Scenario and timing composition is narrower in G0: 20 % S0 and 80 % S3, simultaneous or small, K = 5. Comparisons
  must use matched cells.

## 2. Vehicle identity (`results/vehicle_identity_bias.csv`)

| Group | Attack rate per vehicle ID | χ² uniformity p | Spearman (ID number vs attacked) | Campaign-member position (KS vs uniform) |
|---|---|---|---|---|
| **G0, N = 10** | vehicle_01…05: 1.00; vehicle_06…10: 0.00 | **1.6e-17** | **−0.87** | **p = 6e-29** (members always 1–5) |
| P1, N = 20 / 50 / 100 | 0.29–0.44 / 0.19–0.35 / 0.17–0.28 | 0.96 / 1.00 / 1.00 | 0.03 / 0.01 / −0.01 | p = 0.23 / 0.33 / 0.62 |
| P2, N = 20 / 50 / 100 | 0.38–0.51 / 0.21–0.35 / 0.17–0.30 | 1.00 / 1.00 / 1.00 | −0.01 / −0.01 / −0.00 | p = 0.31 / 0.31 / 0.59 |

* In P1/P2, no vehicle ID is consistently attacked, IDs do not encode attack roles, and membership is uniform over
  positions.
* In G0, identity encodes the attack role perfectly, and the ID format (2 vs 3 digits) reveals G0 membership.
* Detector features contain no vehicle identifiers: in 835/835 sessions the descriptor files hold only keys and the
  24 features, and the model-input allow-list rejects identity columns (`P1_metadata_leakage.md`).

## 3. Pooling decision (`validation/P2_G0_bias_assessment.csv`)

| Use | Recommendation | Reason |
|---|---|---|
| Benign characterisation, benign-only local models, per-window local detection | Pool G0 with P1/P2 (stratify by N where fleet size matters) | No measurable route-source effect on benign CAN or descriptors |
| Fleet-level campaign detection and correlation (FLEET-GUARD, ROBUST-FLEET-GUARD) | **Evaluate G0 separately** and report it as a bridge result; do not pool it into the main campaign metrics | Deterministic first-K identity, a single N and K, and a narrow timing set; any identity-aware component or per-ID aggregation would leak inside G0 |
| Any analysis | Never use vehicle-ID strings or numbers as features | Identity encodes role in G0 |

**Approval needed:** confirm "G0 evaluated separately for campaign-level results" before the main evaluation.
