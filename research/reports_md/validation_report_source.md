# FLEETCAN-SIM Research Dataset — Scientific Validation Report

**Release:** fleetcan-sim-research-1.0, derived from the frozen fleetcan-sim-scale-1.0. **Date:** 2026-10-23.
**Author:** Iyad Atieh, University of Reading.

**Decision: RESEARCH DATASET CONDITIONALLY READY.** All integrity, completeness, leakage, labelling, temporal and
reproducibility checks pass on all 835 sessions, and no genuine data defect was found. Four methodological decisions
need approval before the evaluation: the threshold protocol, the window-label rule, G0 handling and start-up
windows. The data are synthetic.

## 1. Dataset overview

| Property | Value |
|---|---|
| Sessions / configurations / repetitions | 835 / 167 / 5 (frozen matrix SHA-256 9fc005de…e735) |
| CAN frames | 444,333,222 (benign 395,777,880; malicious 48,555,342) |
| Vehicle traces | 48,150 (A 16,435 / B 16,115 / C 15,600) |
| Fleet sizes (sessions) | N = 10 (G0) 25; 20: 245; 50: 270; 100: 295 |
| Scenarios (sessions) | S0 20, S1 60, S2 180, S3 530, S3-mixed 30, S3-probe 15 |
| Attack instances | 12,180 (flooding 3,270; fuzzy 3,090; malfunction 2,755; replay 3,065) |
| Campaigns | 1,895 (575 coordinated, 1,320 independent) |
| Descriptor windows | 8,814,858 (100 frames, stride 50, 24-D) |
| Formats | Parquet (zstd) + gzip CSV for observations, ground truth and descriptors; JSON/CSV metadata |

The release contains detector-safe observations, separate ground truth, separate metadata, descriptors, complete
dataset-wide results, validation tables, a local HTML dashboard and publication documentation. Every table covers all
835 sessions.

![Frames per session by fleet size and scenario](figures/01_frames_per_session.png)

## 2. Generation architecture

SUMO 1.28 provides the mobility. It steps a 4×4 grid network every 10 ms and is read through Python/TraCI. Synthetic
CAN messages are derived from vehicle state by profile-specific DBC encoders. Attacks are injected by a seeded attack
engine scheduled by a campaign scheduler. A logical CAN bus model per vehicle (500 kbit/s, exact stuffing,
arbitration, queue 32, 100 ms deadline) assigns transmission times. Frames are carried over SocketCAN vCAN and matched
frame by frame between send and capture. Ground truth is written from configuration and injection records; it is
never inferred from timing. **SUMO does not generate CAN traffic, and the logical bus is not a physical bus** (no
error frames, retransmissions, bus-off or bit-level effects).

## 3. Vehicle profiles

| Profile | IDs (period ms) | Benign rate | DLC | Notes |
|---|---|---|---|---|
| A | 0x100 (10), 0x120 (20), 0x150 (20), 0x180 (20), 0x200 (10) | 350.09 frames/s | 8 | bytes 4–6 constant 0 |
| B | 0x0C1 (20), 0x0D0 (10), 0x1A0 (10), 0x2B0 (50) | 270.09 frames/s | 8 | bytes 4–6 constant 0 |
| C | 0x101 (10), 0x130 (20), 0x131 (20), 0x190 (20), 0x200 (10) | 350.08 frames/s | 5.71 | per-wheel speeds |

All profiles encode the same physical state with different allocation, periods, phases and packing. They are
synthetic, not OEM models. Profiles are balanced in every fleet.

![Benign CAN-ID periods (mean ± std) per profile](figures/03_benign_can_id_periods.png)

## 4. Benign traffic characterisation

* Every benign ID keeps its nominal period with a coefficient of variation of 0.0082 (±1 % release jitter).
* Benign rates vary by only ≈ 0.03 frames/s across vehicles.
* CAN-ID entropy is constant per profile (2.236 bits for A and C, 1.790 bits for B).
* Driving-dependent payload bytes take all 256 values. Counters take 16 values. A and B have three constant bytes.
* Traffic ramps up during ECU switch-on (U[0, 1] s) and is then flat.
* Per profile, nothing differs measurably across route groups or fleet sizes.

This regularity is an expected synthetic limitation and creates easy shortcuts (FR-04). It was characterised, not
changed.

![Benign rate per vehicle trace](figures/02_benign_rate_by_profile.png)

![Benign traffic over time (S0 sessions)](figures/12_benign_over_time.png)

![Benign payload byte variability](figures/04_payload_byte_std.png)

**Benign-only Isolation Forest sanity check.**
* **Method.** The original implementation was used unchanged: per-profile IF, 200 trees, `random_state` 42, the 24
  features without scaling, and the benign-training percentile normalisation. The split is session-disjoint: 12 S0
  training sessions and 8 held-out S0 sessions with 91,408 windows. No attack data were used.
* **Results.** Pooled held-out FPR at the original strong constant 0.80 is **0.208** (95 % CI 0.205–0.211), against
  0.20 expected by construction for exchangeable benign data. The weak-or-strong band (≥ 0.55) gives 0.453 against
  0.45. Per profile: 0.218 / 0.181 / 0.219. Per N: 0.191 / 0.253 / 0.207 / 0.201.
* **No domain shift.** Held-out FPR matches the training reference, so no train/held-out domain shift is evident.
* **Start-up windows.** First windows per trace are flagged at 98.6 %.
* **Per-vehicle heterogeneity.** Per-vehicle FPR is heterogeneous (median 0.13, maximum 0.92), driven by
  driving-state payload bytes.
* **Threshold not computed.** The original detection threshold needs labelled attack validation data and was not
  computed (approval required, FR-07).

![Benign-only IF false-positive rate](figures/11_benign_if_fpr.png)

## 5. Attack implementation

| Family | Definition | Measured |
|---|---|---|
| Flooding | ID 0x000, DLC 8, zeros, 1600 frames/s | 1600 frames/s exactly; onset 0.25 ms; bus load during attack 0.46–0.49; 75–87 frames per window |
| Fuzzy | random ID/DLC/payload, 1000 frames/s | 1000 frames/s; ≈ 1,871 distinct IDs per instance; 75–80 frames per window |
| Malfunction | speed message = 250 km/h, 100 frames/s, additive | 100 frames/s; ≤ 28 frames per window, **so no majority window** |
| Replay | own-bus frames from [s − T, s), original timing + 0.25 ms | 244–350 frames/s; exactly 50 per window at most (tie) |

Every one of the 12,180 instances has observable frames. There are 0 dropped, expired or skipped frames and 0
malicious frames outside their configured interval or vehicle.

![Observable onset delay by family](figures/05_onset_delay_by_family.png)

![Logical-bus utilisation during attacks](figures/14_bus_utilisation_attack.png)

## 6. Campaign generation

* S3 sessions hold one coordinated campaign of K members: simultaneous; 0.5, 2 or 10 s stagger; partial overlap; or
  bounded spread 0.5, 2 or 10 s for K ≥ 20.
* S3-probe is a 0.1 s replay probe.
* S3-mixed adds independent same-family attacks to a coordinated campaign.
* S2 sessions contain only independent single-target attacks (hard negatives) with matched timing.
* Membership is configured. Members are seeded and profile-balanced, except in G0, which takes the first K vehicles.

In total there are 575 coordinated and 1,320 independent campaigns. Every member of every campaign has observable
malicious frames.

## 7. Dataset-wide statistics

`results/` holds 26 complete tables over all sessions:
* session, vehicle, profile, CAN-ID, DLC, payload, inter-arrival and bus statistics;
* attack instances and family summary;
* campaign instances and timing;
* temporal observability;
* replay timing and links;
* G0 and identity bias;
* scenario and fleet-size summaries;
* descriptor statistics;
* ground-truth checks and data-quality issues.

| Check (all 835 sessions) | Result |
|---|---|
| Source files SHA-256 (26,740) and FREEZE anchors | all OK |
| Manifest completeness, 5 repetitions, generator verdict | 835/835 |
| Schema, formats, ordering, derivability, read-back equality | 835/835 |
| Ground-truth consistency | 835/835 |
| Temporal consistency | 835/835 |
| Bus: dropped / queue / expired frames | 0 / 0 / 0 (max queue delay 254 µs) |

![Descriptor feature shift by window class](figures/13_descriptor_shift.png)

## 8. Metadata-leakage prevention

**Source problem.** The source frames carry `scenario_id`, `session_id`, `frame_uid` and `capture_time_ns`.

**Release design.** Observation files keep only `frame_index`, `timestamp_ns`, `vehicle_id`, `can_id`, `dlc` and
`data` (835/835 verified). Ground truth and metadata are separate trees.

**Detector adapter.** It enforces three allow-lists:
* observations;
* model input: exactly the 24 frozen features;
* graph inputs: no campaign or label columns.

**Tests.** The 22 automated leakage tests pass. They:
* inject 14 kinds of prohibited metadata;
* show that descriptors are invariant to that metadata;
* show the model matrix rejects identity, profile and labels;
* show the graph guard rejects campaign membership;
* show the loader cannot open ground truth.

**Remaining risk.** Absolute time is observable, and attacks start at ≥ 10 s (FR-03).

## 9. Temporal observability

Observable time is the bus end-of-frame (`timestamp_ns`). Per vehicle it is strictly increasing in every trace. The
descriptor timestamp is the last frame of the window (verified for all 8,814,858 windows). Configured and observable
timing are stored separately.

| Family | Onset (median) | First window with ≥ 1 / ≥ 5 attack frames (median) | First majority window (median) |
|---|---|---|---|
| Flooding | 0.25 ms | 13 / 16 ms | 44 ms |
| Fuzzy | 0.16 ms | 19 / 23 ms | 68 ms |
| Malfunction | 0.24 ms | 57 / 98 ms | none |
| Replay | 2.1 ms | 39 / 52 ms | 192 ms |

**Campaign timing.**
* Configured spreads of 0.5 s and more are reproduced at frame and descriptor level, with order agreement 1.000.
  The exception is bounded spread 0.5 s at K up to 100 (gaps 5 ms), where window-level order agreement is 0.960.
* Simultaneous campaigns show a descriptor-level spread of 37 ms (median, maximum 135 ms).
* The 0.1 s probe is resolved in 15/15 sessions.
* **Resolution limit.** About one window stride: 0.14 s for A/C, 0.19 s for B (FR-11).

![Descriptor-level observability](figures/06_first_window_delay.png)

![Configured vs descriptor-level campaign spread](figures/07_campaign_spread.png)

## 10. Replay timing

All 3,065 replay instances were validated:
* links exist;
* ID, payload and bus are identical;
* the time shift is exact;
* originals are benign and precede the start;
* the link set equals the eligible source set;
* the generator's first injection equals the prediction.

Onset discrepancies fall into two classes:
* **Expected replay behaviour (3,055):** 0.43–8.49 ms.
* **Source-frame availability delay (10):** 145–981 ms. These are partial-overlap first members whose replay window
  starts at 0 s, before the source ECU switches on at 0.14–0.98 s.

There were 0 labelling defects and 0 unresolved cases.

![Replay onset vs ECU activation](figures/08_replay_onset.png)

## 11. G0 and vehicle-identity bias

**Route source.** Per profile, G0 against N = 20 non-attacked vehicles gives a maximum |SMD| of 0.24 and a minimum
Mann–Whitney p of 0.12 over benign metrics. For descriptor features the maximum |SMD| is 0.13. No route-source
effect is visible.

**Identity.** In G0, members are always vehicle_01…05 (χ² p = 1.6e-17, Spearman −0.87), and IDs have 2 digits. In
P1/P2 there is no identity bias (χ² p ≥ 0.96, |ρ| ≤ 0.04, member positions uniform).

**Recommendation.** Evaluate G0 separately for campaign-level metrics; pooling is fine for benign and local analyses
(approval required, FR-12).

![G0 vs N = 20 standardised differences](figures/09_G0_vs_N20_smd.png)

![Attack rate by vehicle number](figures/10_attack_rate_by_vehicle_id.png)

## 12. Ground-truth validation

In all 835 sessions:
* frame labels align with frames;
* malicious ⇔ attack ID ⇔ frame source;
* malicious frames appear only on attacked vehicles and inside their configured intervals;
* captured equals labelled;
* generated equals transmitted plus dropped;
* drops equal bus drops (0);
* each family matches its configuration;
* membership equals campaign targets.

Independent attacks are kept distinct from coordinated campaigns. Mixed scenarios contain one coordinated campaign
plus independent attacks.

## 13. Descriptor compatibility

The vectorised release descriptor was compared with the reference FLEET-GUARD implementation on 77 sessions (one per
scenario × family × N × route group): 638,594 windows and 15,326,256 values.
* Window keys, boundaries, timestamps and NaN-fill counts are identical.
* All features are within 1e-9 relative tolerance (96.3 % bit-identical; maximum difference 2.7e-15).
* Window labels are identical.

The original majority rule is reproduced exactly. Its consequences for malfunction and replay make the rule choice
an evaluation decision (FR-08).

## 14. Synthetic-data limitations

* Synthetic data; no production ECU captures; real-world generalisation not established.
* Idealised logical bus with no frame loss.
* Low benign variability.
* Predictable warm-up and fixed attack start of ≥ 10 s.
* Non-saturating flooding and additive malfunction.
* Fixed durations.
* Reduced-factorial coverage.
* Sub-window timing is not resolvable.
* Start-up windows are anomalous by construction.
* G0 identity is deterministic.

The full list with measurements is in `documentation/KNOWN_LIMITATIONS.md`.

## 15. Reproducibility

* **Inputs.** Source SHA256SUMS `5d5418b1…de70`, matrix `9fc005de…e735`.
* **Code.** `research/` in `iyadsec/fleet-can-dataset`; the commit is in `documentation/BUILD_INFO.json`; copies are
  in `scripts/`.
* **Determinism and safety.**
  * Processing is deterministic and streaming (one session at a time, bounded memory).
  * Writes are atomic and the run is resumable.
  * The benign IF was run twice with identical scores.
* **Integrity.** Output integrity is in `MANIFEST.csv` and `SHA256SUMS` (re-verified). The source was re-hashed
  after export and is unchanged.

## 16. Final research-readiness assessment

| Requirement | Status |
|---|---|
| Source integrity; 835 sessions; 444,333,222 frames | PASS |
| Detector-safe schema; leakage prevention (tests); ground-truth separation | PASS |
| Benign characterisation; benign-only FPR check | PASS (threshold protocol open) |
| Observable and descriptor timestamps; window labelling | PASS (label-rule choice open) |
| Replay relationships and onset discrepancies | PASS |
| G0 route bias; vehicle-identity bias | assessed (G0 handling open) |
| Campaign timing; dataset-wide statistics | PASS |
| Full CSV and Parquet exports; HTML; PDF; documentation; checksums | PASS |
| Frozen source unchanged; critical unresolved data defects | PASS / none |

**RESEARCH DATASET CONDITIONALLY READY.** These four decisions must be approved before the FLEET-GUARD /
ROBUST-FLEET-GUARD evaluation:
* FR-07: leakage-free threshold protocol;
* FR-08: window-label rule;
* FR-12: separate G0 evaluation for campaign metrics;
* FR-06: handling of start-up windows.

None of them requires a change to the data.
