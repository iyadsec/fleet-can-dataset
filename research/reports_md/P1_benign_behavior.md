# P1 — Benign CAN traffic: characterisation and limited false-positive sanity check

**Result: characterisation complete for all 835 sessions (48,150 vehicle traces, 395,777,880 benign frames). The
benign-only Isolation Forest check is complete and documented.** Benign traffic is highly regular by design. This is
a synthetic-data limitation (category D) and is not changed. No train/held-out domain shift was found. Two systematic
false-positive sources are identified: start-up windows and per-vehicle payload state.

## 1. Characterisation (`results/benign_traffic_statistics.csv`, `vehicle_profile_summary.csv`, `can_id_statistics.csv`, `payload_statistics.csv`, `dlc_statistics.csv`, `interarrival_statistics.csv`, `benign_traffic_over_time.csv`)

| Profile | Vehicles | Benign IDs | Benign rate (frames/s), mean ± std across vehicles | ID entropy (bits) | DLC | Constant payload bytes | Mean per-byte std |
|---|---|---|---|---|---|---|---|
| A | 16,435 | 5 (0x100, 0x120, 0x150, 0x180, 0x200) | 350.09 ± 0.034 | 2.236 | 8 | bytes 4–6 always 0 | 28.3 |
| B | 16,115 | 4 (0x0C1, 0x0D0, 0x1A0, 0x2B0) | 270.09 ± 0.029 | 1.790 | 8 | bytes 4–6 always 0 | 33.6 |
| C | 15,600 | 5 (0x101, 0x130, 0x131, 0x190, 0x200) | 350.08 ± 0.035 | 2.236 | 5.71 (mixed) | none | 48.7 |

* **Periodicity.** Every benign ID keeps its nominal period (10, 20 or 50 ms) with a coefficient of variation of
  0.0082 (±1 % uniform release jitter). Periods stay within ±4.5 % of nominal across all vehicles.
* **CAN-ID diversity.** There are 4–5 IDs per profile and 14 benign IDs in total. ID entropy is essentially constant
  per profile (std < 1e-4 bits).
* **Payload variability.** Driving-dependent bytes take all 256 values. Counters cycle through 16 values. Profiles A
  and B carry three constant bytes.
* **Time evolution.** Traffic is zero before ECU switch-on (U[0, 1] s, mean 0.50 s), ramps up while streams start
  at their phases, and is then flat for the rest of the session. Attacks start at ≥ 10 s, so the benign warm-up is
  predictable.
* **Route groups and fleet sizes.** Per-profile benign rates, entropies and byte variability are indistinguishable
  across G0/P1/P2 and N = 10/20/50/100. Pooled differences, such as G0's 326.1 vs 323.3 frames/s, reflect profile
  composition only (see `P2_G0_bias.md`).
* **Bus.** Benign load is ≈ 6.5–8.4 % per bus. There were 0 dropped or expired frames in any session.

**Shortcut risks.** With so little benign variability, any change in ID set, rate or DLC is trivially separable.
Profile differences (B: 4 IDs, 270 frames/s) would also let a pooled model separate profiles, which is why the
original method uses per-profile models. Absolute detector performance on this dataset does not transfer to real
traffic (FR-04).

## 2. Benign-only Isolation Forest sanity check (`results/benign_false_positive_statistics.csv`, `validation/P1_benign_sanity_check.csv`)

**Implementation.** The repository's audited reproduction of FLEET-GUARD's local IDS was used unchanged
(`detection/model.py`, `features.py`, `split.py`; `docs/m7_local_if_protocol.md`):

* one IsolationForest per profile, with `n_estimators = 200`, `contamination = "auto"` and `random_state = 42`;
* exactly the 24 features, `fillna(0)`, float32, no scaling;
* scores normalised by the empirical CDF of the model's own benign training scores.

**Split.** The split is session-disjoint and deterministic (seed 42). Each of the four S0 configurations
(N = 10 G0, 20, 50, 100) contributes 3 training sessions, 1 validation session and 1 test session. That gives 12
training sessions (137,230 windows) and 8 held-out sessions (91,408 windows, 360 vehicles). No attack data were used.

**Thresholds.** The original detection threshold (P1: "validation FPR ≤ 5 %, highest recall") **requires labelled
attack windows** in the validation partition. It was therefore **not computed**. A leakage-free pre-evaluation
protocol needs approval (FR-07). Only the original label-free evidence constants of the fleet pipeline were applied:
strong ≥ 0.80 and weak ≥ 0.55. Nothing was tuned.

| Group | Held-out windows | FPR strong (≥ 0.80) | 95 % CI | FPR weak or strong (≥ 0.55) |
|---|---|---|---|---|
| **Pooled** | 91,408 | **0.208** | 0.205–0.211 | 0.453 |
| Profile A / B / C | 34,098 / 25,400 / 31,910 | 0.218 / 0.181 / 0.219 | — | 0.477 / 0.430 / 0.445 |
| N = 10 (G0) / 20 / 50 / 100 | 5,134 / 10,105 / 25,308 / 50,861 | 0.191 / 0.253 / 0.207 / 0.201 | — | 0.391 / 0.486 / 0.452 / 0.453 |
| Route group G0 / P1 | 5,134 / 86,274 | 0.191 / 0.209 | — | 0.391 / 0.456 |
| Training (in-sample reference) | 137,230 | 0.200 | — | 0.450 |
| First window of each trace | 360 | **0.986** | — | 1.000 |
| Windows 2–5 / 6–20 / > 20 | 1,440 / 5,400 / 84,208 | 0.397 / 0.138 / 0.206 | — | 0.796 / 0.602 / 0.435 |

The two runs gave identical scores (maximum score difference 0.0).

## 3. Interpretation

1. **The ≈ 20 % strong-band FPR is detector behaviour, not a dataset defect.** The normalised score is the
   percentile of a window among the benign training windows. If held-out benign windows come from the same
   distribution, about 20 % of them score ≥ 0.80 and about 45 % score ≥ 0.55 by construction. The observed
   0.208 / 0.453 match these reference values, so there is **no evidence of domain shift** between training and
   held-out benign sessions, profiles or route groups. The original constants are percentile bands, not calibrated
   alarm thresholds (FR-05).
2. **Start-up windows.** Of the first windows of all traces, 98.6 % are flagged strong, and windows 2–5 are flagged
   at 40 %. The first windows cover the ECU switch-on, when streams start one by one at their phase offsets. That
   pattern is rare in training (one window per trace). It is a predictable artefact of the synthetic start-up. It
   needs an evaluation decision: exclude, stratify, or keep and report (FR-06).
3. **Per-vehicle heterogeneity.** Median vehicle FPR is 0.13, but individual vehicles reach 0.92. Strong held-out
   windows deviate mostly in payload-byte statistics (`results/benign_feature_deviation.csv`: `byte_mean_2/3` and
   `byte_std_2/3` with |z| ≈ 1.3–1.8). These bytes encode driving state (speed, RPM, pedal), so vehicles whose
   trajectory differs from the training vehicles concentrate the alerts. This is expected detector behaviour on
   state-dependent payloads, not an implementation problem.
4. **No implementation problem found.** The split is session-disjoint, training is benign only, the hyperparameters
   and normalisation are the original ones, and the run is reproducible.

No data were regenerated or modified because of these results.
