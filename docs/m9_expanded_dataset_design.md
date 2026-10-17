# Milestone 9 — expanded dataset experimental design and matrix freeze proposal

**Status: DESIGN READY FOR REVIEW.** Nothing in this milestone generated CAN data, created a session, or touched
fleetcan-sim-1.1. The proposed matrix `configs/m9_expanded_matrix_proposed.yaml` carries
`approved_for_generation: false` and no runner reads it (validator check M01).
No detector output (M7 Isolation Forest or otherwise) was used for any design decision.

Artifacts:

| File | Content |
|---|---|
| `scripts/m9_design.py` | candidate designs A/B/C, measured cost model, member-selection rule, YAML/CSV/table writer (`--write`), cost-model calibration (`--calibrate`) |
| `configs/m9_expanded_matrix_proposed.yaml` | PROPOSED matrix of design B (162 configurations × 5 = 810 sessions), seeds, session IDs, explicit delay vectors |
| `scripts/validate_m9_matrix.py` | read-only validator (14 checks, below); never writes into `data/` |
| `results/m9_design/` | `design_comparison.json`, `configs_{A,B,C}*.csv`, `members_B_balanced.csv` (targets and K_A/K_B/K_C of all 810 sessions), `cost_model_calibration.csv`, `tables_B_balanced.md`, `validation_report.json` |
| `tests/test_m9.py` | 18 tests (validator negative tests, selection rule, timing semantics, seed namespaces) |

## 1. Starting point and fixed constraints

* Baseline **fleetcan-sim-1.1** (immutable): 122 configurations × 5 = 610 sessions, N = 2/5/10, generator 1.1.0,
  frozen SUMO scenario (10 routes), IDs `vehicle_01..`, seeds 610001–611215, session IDs `FC11_*`.
  SHA256SUMS `bc523937…`, FREEZE.json `df58000a…`, manifest `aebfd8e0…`, matrix spec `4e6dd99d…` — verified unchanged
  after M9 (20,805/20,805 files OK).
* Expanded sizes **N = 20/50/100** need generator ≥ 1.2.0 (opt-in route generation `grid_loops_v1`, 3-digit IDs)
  and are run with generator **1.3.0** (bounded memory, M8.1), which reproduces generator 1.1.0/1.2.0 outputs
  bit-for-bit wherever both exist (M8.1 equivalence: 10 frozen N = 10 sessions, N = 20/50/100 M8 sessions).
* Frozen semantics reused unchanged: profiles A/B/C (`balanced_shuffle`), switch-on U[0, 1] s @ 1 µs, ±1 % ECU jitter,
  500 kbit/s bus model, the four attack families and their parameters, 24-D descriptors (100-frame windows, stride 50).

## 2. Existing timing semantics (inspected, documented exactly)

The generator's campaign scheduler (`src/campaign_scheduler.py`) knows three delay patterns: `simultaneous`
(all relative delays 0), `fixed` (explicit `relative_delays_s`, one per target, in target order) and `uniform_random`.
**Sequential stagger is not a generator concept**: the frozen matrix builder (`scripts/m6_build_matrix.py`) turns
"0.5 s / 2 s / 10 s" into a `fixed` campaign with

  delay(member i) = Δ · i,  i = 0 … K−1 in the configured target order  → total spread (K−1)·Δ.

| Pattern (frozen) | Δ | attack duration | definition | example K = 5 (campaign start 10 s) |
|---|---|---|---|---|
| simultaneous | 0 | 5 s | all members start at 10 s | spread 0, session 20 s |
| small | 0.5 s | 5 s | 10 + 0.5 i | spread 2 s, session 22 s |
| medium | 2 s | 5 s | 10 + 2 i | spread 8 s, session 28 s |
| large | 10 s | 5 s | 10 + 10 i (no two members overlap) | spread 40 s, session 60 s |
| partial | 5 s | **10 s** | 10 + 5 i; member i active [10+5i, 20+5i]; consecutive members overlap exactly 5 s (50 % of each attack), members i and i+2 do not overlap | spread 20 s, campaign [10, 40] s, session 45 s |
| probe | 0.1 s | 5 s | replay, K = 2, delays [0, 0.1] s | session 20.1 s |

Other frozen rules: attacks start at 10 s (warm-up), session duration = 10 + max delay + attack duration + 5 s
(S0 40 s, S1 20 s); replay uses `window_relative_s = [−5, 0]` relative to each member's own start, source = own bus,
+0.25 ms offset, so a replay member ends 0.25 ms after the nominal 5 s (the frozen duration rule leaves a tail of
5 s − 0.25 ms; the validator allows exactly that); S2: k independent single-target campaigns (`coordinated: false`),
starts evenly spread over [10.0, 10.5] s; mixed: coordinated `small` campaign + m same-family independent attacks at
10.25 + 0.5 j s.

**Large-K consequence.** Applying the frozen rule at K = 100 gives spreads of 49.5 s (0.5 s), 198 s (2 s) and
990 s (10 s, 1,010-s sessions). With 5-s attacks, Δ ≥ 5 s means no two members are ever active together: a
"10 s stagger" at K = 100 is 100 consecutive isolated incidents, a different scientific object from the baseline
K ≤ 10 campaigns. The frozen rule is therefore **kept unchanged where it is meaningful (K ≤ 20 for 0.5 s, K = 5 for
2/10 s/partial)** and a **NEW, explicitly named** large-K condition is added:

  **bounded_spread_W (NEW expanded-dataset condition):** delay(member i) = W · i / (K − 1), W ∈ {0.5, 2, 10} s —
  total campaign spread W independent of K (`bounded_spread_0p5s`, `bounded_spread_2s`, `bounded_spread_10s`).

It is realised as a `fixed` campaign (no generator change) and never relabels a frozen pattern. At K = 20,
`bounded_spread_10s` (step 0.526 s, spread 10 s) almost coincides with frozen `small` (step 0.5 s, spread 9.5 s);
that cell is kept on purpose as a bridge between the two definitions. Configured times remain ground truth only;
any later temporal-correlation analysis must use observable timestamps.

## 3. Seed and RNG architecture (verified, unchanged) and new streams

| Stream | Derivation | Status |
|---|---|---|
| master (per session) | `seed = 920000 + 10 · config_index + repetition`, repetition 1..5 → 920001 … 921615 | NEW range; baseline 610001–611215, M8/M8.1 test seeds 80010–83020, all 668 existing seeds checked (M09) |
| SUMO driver imperfection | `--seed seed` | frozen |
| route generation | `derive_seed(seed, "route_generation")` (grid_loops_v1) | frozen (1.2.0) |
| profile assignment | `derive_seed(seed, "profile_assignment")` | frozen |
| switch-on | `derive_seed(seed, "switch_on")` | frozen |
| ECU jitter | `derive_seed(seed, "ecu_jitter")` → per stream `derive_seed(jseed, "ecu_jitter/<vehicle>/<message>")` | frozen |
| attack | `SeedSequence([seed, crc32(attack_id)])`, attack_id = `<campaign_id>_<vehicle_id>` | frozen |
| campaign | `SeedSequence([seed, crc32("campaign:" + campaign_id)])` | frozen |
| **coordinated members** | `derive_seed(seed, "campaign_members/C_MAIN")` | **NEW** |
| **independent targets** (S2, mixed) | `derive_seed(seed, "independent_targets")` | **NEW** |
| **isolated target** (S1) | `derive_seed(seed, "isolated_target")` | **NEW** |

`derive_seed(seed, tag) = SeedSequence([seed, crc32(tag)]).generate_state(1)[0]`. The new streams are consumed only
by the (future) matrix builder, which writes explicit `targets` into each session's campaign file; the generator
never draws from them, so no existing stream is disturbed. CRC-32 values of the new tags differ from all existing
tags (test). No detector-derived randomness exists anywhere.

## 4. Campaign-member selection rule (deterministic, detector-free)

For a session with seed s and its frozen fleet (profile assignment from `derive_seed(s, "profile_assignment")`):

1. **Quotas (profile-balanced).** P = 3 profiles; each gets ⌊k/3⌋; the k mod 3 extra places go to profiles in a
   seeded random order; a quota above a profile's eligible vehicles is capped and moved to the next profile in the
   same order. Result: K_A, K_B, K_C differ by at most 1 (all 810 sessions, M12).
2. **Vehicles.** Seeded choice without replacement from each profile's sorted vehicle IDs.
3. **Order = delay rank.** Seeded permutation of the chosen vehicles, so the member that starts first is not tied
   to profile or vehicle ID.
4. **Eligibility.** Independent targets in mixed sessions are drawn from the vehicles not in the coordinated
   campaign (separate stream); S1 uses the same rule with k = 1 (uniform over profiles, then over vehicles).

Inputs are the experiment seed and the frozen fleet only. The rule does not prefer `vehicle_001 … vehicle_K`,
profiles, or "easy/hard" vehicles; a proportional largest-remainder rule was rejected because it never selects the
smallest profile group at K = 1–2 (e.g. N = 20: A 7, B 7, C 6). Per-session targets and K_A/K_B/K_C:
`results/m9_design/members_B_balanced.csv`.
*Deliberate difference from the baseline:* fleetcan-sim-1.1 attacked `vehicle_01 … vehicle_k`; because the baseline
profile assignment is itself seeded, those vehicles already had random profiles, so the change affects only the
vehicle identity, not the profile distribution.

## 5. N versus K, penetration ρ = K/N, and the nested N/K grid

N (fleet size) and K (coordinated campaign size) are independent design variables. The candidate nested grid
(K ∈ {2, 5, 10, 20, 50, 100}, K ≤ N) was analysed cell by cell. Every new cell belongs to at least one of three
contrasts that a smaller grid cannot provide:

* **iso-K rows** (fleet scaling): K = 2, 5, 10 across N = 2/5/10 (baseline) … 100; K = 20 across N = 20/50/100;
* **iso-N columns** (campaign scaling): N = 100 with K = 2 … 100, N = 50 with K = 2 … 50;
* **iso-ρ diagonals** (penetration vs absolute size): ρ = 0.1 at (20,2), (50,5), (100,10); ρ = 0.2 at (10,2)b,
  (50,10), (100,20); ρ = 0.5 at (10,5)b, (20,10), (100,50); ρ = 1 at all six N.

Hence the nested grid is **accepted for the simultaneous core only** (all 15 new N/K cells). It is **not** crossed
with every timing pattern or attack family (section 7). Penetrations represented: 0.02 … 1 (table below), i.e.
small and large absolute K, low (≤ 0.05), medium (0.1–0.5) and full-fleet (1) penetration; N = 5, K = 5 and
N = 100, K = 100 both have ρ = 1 at radically different absolute scale.

A second meaning of "nested" — sessions sharing one seed so that the K = 5 members are a subset of the K = 10
members over identical benign traffic — was evaluated and **rejected** for the primary matrix: it would break the
unique-seed rule, create cross-configuration dependence (identical benign traffic in different configurations, a
leakage risk for any split), and is unnecessary for main effects estimated over 5 repetitions. It can be added later
as an explicitly paired sub-study with grouped splits if a reviewer requires paired K contrasts.

## 6. Candidate designs (costs from measured M8/M8.1 data)

Cost model (generator 1.3.0, `scripts/m9_design.py`, coefficients measured in M8/M8.1):
frames = 320 · N · duration + members · rate · attack_s (flooding 1600, fuzzy 1000, malfunction 100, replay ≈ 324
frames/s per member); generation 2.5 s + 127 µs/frame; analysis 0.5 s + 17.5 µs/frame; storage 52 MB per 10⁶
frames + 55 MB per 10⁶ replay frames (replay map); descriptors 0.0199 per frame.
**Calibration against all 22 measured N ≥ 20 sessions** (`cost_model_calibration.csv`; S0 20–180 s, campaigns with
K = 10/20/100, S2, all four families at N = 100): frames −0.8 … +1.9 % (mean +0.3 %), storage −1.4 … +6.7 %
(mean +2.5 %), wall time −3.9 … +10.1 % (mean +3.0 %) — slightly conservative.
**Interpolation/extrapolation:** designs A and B lie inside the measured range (N 20–100, durations 20–60 s ≤ 180 s
measured, K ≤ 100 measured for replay). Mild extrapolation in attack load only: flooding/fuzzy campaigns with
K = 50/100 were not measured (largest measured: 100-member replay, 10-member fuzzy); per-frame cost is assumed
constant. Design C extrapolates strongly (sessions up to 1,010 s, 5.6 × the longest measured).

| Design | Idea | configs | sessions | simulated h | frames | descriptors | storage | gen + analysis (sequential) |
|---|---|---|---|---|---|---|---|---|
| A minimal | S0 at 3 N; S1 only at N = 100; K ladder only at N = 100 and K = 5 at N = 20/50 (simultaneous); timing ladder only at N = 100, K = 5; 2 families (flooding, replay) in S2/S3 | 39 | 195 | 1.4 | 146 M | 2.9 M | 7.7 GB | 6.0 h |
| **B balanced** | nested N × K core (simultaneous), 0.5 s grid K ≤ 20, frozen ladder at K = 5 across N, NEW bounded spread for K ≥ 20, three hard-negative timings, mixed, probe; balanced family rotation | **162** | **810** | **5.9** | **446 M** | **8.9 M** | **23.5 GB** | **18.6 h** |
| C comprehensive | every K ≤ N × 5 frozen patterns (+3 bounded spreads for K ≥ 20) × 4 families; S2 k ∈ {2…50} × 2 timings × 4; mixed variants | 518 | 2,590 | 44.7 | 3,958 M | 78.8 M | 207 GB | 161 h |

Why not A: it cannot separate N and K effects except at K = 5 (no iso-N column at N = 50, no iso-ρ diagonals), has
no staggered/separated hard negatives, half the attack families, no bounded-spread condition, and S1 at one N only.
Why not C: 207 GB (≈ 11 × the free disk), 161 h, frozen 10 s stagger at K = 100 (1,010-s sessions of 100
non-overlapping incidents — scientifically a different object), and most added cells are higher-order interactions
(family × timing × K × N) that the research questions do not require.
**Recommendation: design B** — it covers all three contrasts (iso-K, iso-N, iso-ρ), keeps every frozen timing
anchor, gives each negative control a matched coordinated counterpart, and represents every attack family in every
configuration family, at ≈ 1/9 of C's cost.

## 7. Recommended design B — construction rules

| Family | Rule | Families per cell (reduced factorial) |
|---|---|---|
| S0_scale | one benign configuration per new N, 40 s | — |
| S1_isolated | one seeded vehicle, attack 10–15 s, 20 s session, at N = 20/50/100 | all 4 |
| S2_hardneg_near_simultaneous | k ∈ {5, 10} independent same-family attacks, starts over [10, 10.5] s (frozen S2) | all 4 at k = 5; 2 (rotation) at k = 10 |
| S2_hardneg_staggered (NEW timing) | k ∈ {5, 10}, starts 10 + 0.5 j — identical timing to coordinated `small` | the 2 families of the matched `small` cell (same N, K) |
| S2_hardneg_separated (NEW timing) | k = 5, starts 10 + 10 j (no overlap) — identical timing to coordinated `large` K = 5 | the 2 families of the matched `large` cell |
| S3_NK_grid_simultaneous | all K ≤ N in {2, 5, 10, 20, 50, 100} | all 4 at K = 5 and K = N; 2 (rotation) elsewhere |
| S3_NK_grid_small_stagger | frozen 0.5 s, K ∈ {2, 5, 10, 20} | 2 (rotation) |
| S3_temporal_ladder_K5 | frozen 2 s / 10 s / partial at K = 5, every new N | 2 (rotation) |
| S3_bounded_spread_largeK (NEW) | W ∈ {0.5, 2, 10} s for every K ∈ {20, 50, 100} ≤ N | 1 (rotation over the 18 cells) |
| S3_probe_replay | frozen 0.1 s replay probe, K = 2 | replay |
| S3_mixed | coordinated `small` K = 5 (N = 20) / 10 (N = 50, 100) + m = 2 / 5 / 5 independent same-family attacks at 10.25 + 0.5 j s | 2 per N (rotation) |

**Reduced factorial.** Attack family enters through a deterministic rotation `latin(i) = families[i mod 4],
families[(i+1) mod 4]` indexed by (N, K, pattern), so neighbouring cells get different pairs. Every family appears in
every configuration family (except the replay-only probe), each family has 38–41 configurations overall, and the
core cells (S1, S2 k = 5, S3 K = 5 and K = N) carry all four families at every N. The four families exercise the
*local* evidence (signature); their interaction with N, K and timing is second-order for fleet-level correlation and
is sampled, not crossed exhaustively.

**Phases (for disk planning only; the design is one matrix).** Phase 1 = S0, S1, S2 near-simultaneous, N × K
simultaneous grid, probe, mixed: 84 configurations / 420 sessions / 9.9 GB / 7.9 h. Phase 2 = 0.5 s grid, staggered
and separated hard negatives, K = 5 ladder, bounded spread: 78 / 390 / 13.5 GB / 10.7 h.

Generated by `scripts/m9_design.py --write` (also in `results/m9_design/tables_B_balanced.md`). Baseline cells (b) count fleetcan-sim-1.1 configurations incl. mixed and probe.

### Matrix table (recommended design B, one row per configuration family x N)

| config_family | scenario | N | K | attack_type | temporal_pattern | independent_attack_count | mixed_attack_count | duration_s | repetitions | configs | sessions | phase | purpose |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S0_scale | S0 | 20 | 0 | none | none | 0 | 0 | 40 | 5 | 1 | 5 | 1 | benign control and false-correlation control at each new N |
| S0_scale | S0 | 50 | 0 | none | none | 0 | 0 | 40 | 5 | 1 | 5 | 1 | benign control and false-correlation control at each new N |
| S0_scale | S0 | 100 | 0 | none | none | 0 | 0 | 40 | 5 | 1 | 5 | 1 | benign control and false-correlation control at each new N |
| S1_isolated | S1 | 20 | 0 | all 4 | isolated | 1 | 0 | 20 | 5 | 4 | 20 | 1 | one attacked vehicle among N: single incident must not look coordinated |
| S1_isolated | S1 | 50 | 0 | all 4 | isolated | 1 | 0 | 20 | 5 | 4 | 20 | 1 | one attacked vehicle among N: single incident must not look coordinated |
| S1_isolated | S1 | 100 | 0 | all 4 | isolated | 1 | 0 | 20 | 5 | 4 | 20 | 1 | one attacked vehicle among N: single incident must not look coordinated |
| S2_hardneg_near_simultaneous | S2 | 20 | 0 | floox2, fuzzx1, malfx1, replx2 | near_simultaneous | 5/10 | 0 | 20.5 | 5 | 6 | 30 | 1 | hard negative: same family, k vehicles, starts within 0.5 s, coordinated=false |
| S2_hardneg_near_simultaneous | S2 | 50 | 0 | floox2, fuzzx2, malfx1, replx1 | near_simultaneous | 5/10 | 0 | 20.5 | 5 | 6 | 30 | 1 | hard negative: same family, k vehicles, starts within 0.5 s, coordinated=false |
| S2_hardneg_near_simultaneous | S2 | 100 | 0 | floox1, fuzzx2, malfx2, replx1 | near_simultaneous | 5/10 | 0 | 20.5 | 5 | 6 | 30 | 1 | hard negative: same family, k vehicles, starts within 0.5 s, coordinated=false |
| S2_hardneg_staggered | S2 | 20 | 0 | fuzzx1, malfx2, replx1 | staggered_0.5s | 5/10 | 0 | 22-24.5 | 5 | 4 | 20 | 2 | hard negative matched to coordinated 'small' (same N, k = K, same families) |
| S2_hardneg_staggered | S2 | 50 | 0 | floox1, malfx1, replx2 | staggered_0.5s | 5/10 | 0 | 22-24.5 | 5 | 4 | 20 | 2 | hard negative matched to coordinated 'small' (same N, k = K, same families) |
| S2_hardneg_staggered | S2 | 100 | 0 | floox2, fuzzx1, replx1 | staggered_0.5s | 5/10 | 0 | 22-24.5 | 5 | 4 | 20 | 2 | hard negative matched to coordinated 'small' (same N, k = K, same families) |
| S2_hardneg_separated | S2 | 20 | 0 | floox1, fuzzx1 | separated_10s | 5 | 0 | 60 | 5 | 2 | 10 | 2 | hard negative matched to coordinated 'large' K = 5 (no temporal overlap) |
| S2_hardneg_separated | S2 | 50 | 0 | fuzzx1, malfx1 | separated_10s | 5 | 0 | 60 | 5 | 2 | 10 | 2 | hard negative matched to coordinated 'large' K = 5 (no temporal overlap) |
| S2_hardneg_separated | S2 | 100 | 0 | malfx1, replx1 | separated_10s | 5 | 0 | 60 | 5 | 2 | 10 | 2 | hard negative matched to coordinated 'large' K = 5 (no temporal overlap) |
| S3_NK_grid_simultaneous | S3 | 20 | 2/5/10/20 | floox3, fuzzx3, malfx3, replx3 | simultaneous | 0 | 0 | 20 | 5 | 12 | 60 | 1 | N x K core grid: separates fleet-size and campaign-size effects |
| S3_NK_grid_simultaneous | S3 | 50 | 2/5/10/20/50 | floox4, fuzzx4, malfx3, replx3 | simultaneous | 0 | 0 | 20 | 5 | 14 | 70 | 1 | N x K core grid: separates fleet-size and campaign-size effects |
| S3_NK_grid_simultaneous | S3 | 100 | 2/5/10/20/50/100 | floox3, fuzzx4, malfx5, replx4 | simultaneous | 0 | 0 | 20 | 5 | 16 | 80 | 1 | N x K core grid: separates fleet-size and campaign-size effects |
| S3_NK_grid_small_stagger | S3 | 20 | 2/5/10/20 | floox2, fuzzx2, malfx2, replx2 | small | 0 | 0 | 20.5-29.5 | 5 | 8 | 40 | 2 | N x K grid under 0.5 s sequential stagger (frozen) |
| S3_NK_grid_small_stagger | S3 | 50 | 2/5/10/20 | floox2, fuzzx2, malfx2, replx2 | small | 0 | 0 | 20.5-29.5 | 5 | 8 | 40 | 2 | N x K grid under 0.5 s sequential stagger (frozen) |
| S3_NK_grid_small_stagger | S3 | 100 | 2/5/10/20 | floox2, fuzzx2, malfx2, replx2 | small | 0 | 0 | 20.5-29.5 | 5 | 8 | 40 | 2 | N x K grid under 0.5 s sequential stagger (frozen) |
| S3_temporal_ladder_K5 | S3 | 20 | 5 | floox1, fuzzx1, malfx2, replx2 | large/medium/partial | 0 | 0 | 28-60 | 5 | 6 | 30 | 2 | frozen timing ladder at fixed K = 5 across N (fleet scaling of temporal evidence) |
| S3_temporal_ladder_K5 | S3 | 50 | 5 | floox2, fuzzx1, malfx1, replx2 | large/medium/partial | 0 | 0 | 28-60 | 5 | 6 | 30 | 2 | frozen timing ladder at fixed K = 5 across N (fleet scaling of temporal evidence) |
| S3_temporal_ladder_K5 | S3 | 100 | 5 | floox2, fuzzx2, malfx1, replx1 | large/medium/partial | 0 | 0 | 28-60 | 5 | 6 | 30 | 2 | frozen timing ladder at fixed K = 5 across N (fleet scaling of temporal evidence) |
| S3_bounded_spread_largeK | S3 | 20 | 20 | floox1, fuzzx1, malfx1 | bounded_spread_0p5s/bounded_spread_10s/bounded_spread_2s | 0 | 0 | 20.5-30 | 5 | 3 | 15 | 2 | NEW bounded spread W = 0.5 / 2 / 10 s for K >= 20 |
| S3_bounded_spread_largeK | S3 | 50 | 20/50 | floox2, fuzzx1, malfx1, replx2 | bounded_spread_0p5s/bounded_spread_10s/bounded_spread_2s | 0 | 0 | 20.5-30 | 5 | 6 | 30 | 2 | NEW bounded spread W = 0.5 / 2 / 10 s for K >= 20 |
| S3_bounded_spread_largeK | S3 | 100 | 20/50/100 | floox2, fuzzx3, malfx2, replx2 | bounded_spread_0p5s/bounded_spread_10s/bounded_spread_2s | 0 | 0 | 20.5-30 | 5 | 9 | 45 | 2 | NEW bounded spread W = 0.5 / 2 / 10 s for K >= 20 |
| S3_probe_replay | S3-probe | 20 | 2 | replx1 | probe | 0 | 0 | 20.1 | 5 | 1 | 5 | 1 | 0.1 s replay temporal-resolution probe |
| S3_probe_replay | S3-probe | 50 | 2 | replx1 | probe | 0 | 0 | 20.1 | 5 | 1 | 5 | 1 | 0.1 s replay temporal-resolution probe |
| S3_probe_replay | S3-probe | 100 | 2 | replx1 | probe | 0 | 0 | 20.1 | 5 | 1 | 5 | 1 | 0.1 s replay temporal-resolution probe |
| S3_mixed | S3-mixed | 20 | 5 | floox1, fuzzx1 | small | 0 | 2 | 22 | 5 | 2 | 10 | 1 | coordinated campaign + unrelated independent attacks in one fleet |
| S3_mixed | S3-mixed | 50 | 10 | malfx1, replx1 | small | 0 | 5 | 24.5 | 5 | 2 | 10 | 1 | coordinated campaign + unrelated independent attacks in one fleet |
| S3_mixed | S3-mixed | 100 | 10 | floox1, fuzzx1 | small | 0 | 5 | 24.5 | 5 | 2 | 10 | 1 | coordinated campaign + unrelated independent attacks in one fleet |
| **total** | | | | | | | | | | **162** | **810** | | |

### N x K coverage of coordinated campaigns (configurations; baseline cells marked b)

| N \ K | 2 | 5 | 10 | 20 | 50 | 100 |
|---|---|---|---|---|---|---|
| 2 | b21 (large:4, medium:4, partial:4, probe:1, simultaneous:4, small:4) | - | - | - | - | - |
| 5 | b13 (probe:1, simultaneous:4, small:8) | b20 (large:4, medium:4, partial:4, simultaneous:4, small:4) | - | - | - | - |
| 10 | b9 (probe:1, simultaneous:4, small:4) | b12 (simultaneous:4, small:8) | b20 (large:4, medium:4, partial:4, simultaneous:4, small:4) | - | - | - |
| 20 | 5 (probe:1, simultaneous:2, small:2) | 14 (large:2, medium:2, partial:2, simultaneous:4, small:4) | 4 (simultaneous:2, small:2) | 9 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:4, small:2) | - | - |
| 50 | 5 (probe:1, simultaneous:2, small:2) | 12 (large:2, medium:2, partial:2, simultaneous:4, small:2) | 6 (simultaneous:2, small:4) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:2, small:2) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:4) | - |
| 100 | 5 (probe:1, simultaneous:2, small:2) | 12 (large:2, medium:2, partial:2, simultaneous:4, small:2) | 6 (simultaneous:2, small:4) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:2, small:2) | 5 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:2) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:4) |

### Penetration rho = K / N represented (coordinated campaigns)

| rho | (N, K) cells |
|---|---|
| 0.02 | (100,2) |
| 0.04 | (50,2) |
| 0.05 | (100,5) |
| 0.1 | (20,2) (50,5) (100,10) |
| 0.2 | (10,2)b (50,10) (100,20) |
| 0.25 | (20,5) |
| 0.4 | (5,2)b (50,20) |
| 0.5 | (10,5)b (20,10) (100,50) |
| 1 | (2,2)b (5,5)b (20,20) (50,50) (10,10)b (100,100) |

### Attack family x configuration family (configurations)

| config_family | flooding | fuzzy | malfunction | replay |
|---|---|---|---|---|
| S1_isolated | 3 | 3 | 3 | 3 |
| S2_hardneg_near_simultaneous | 5 | 5 | 4 | 4 |
| S2_hardneg_staggered | 3 | 2 | 3 | 4 |
| S2_hardneg_separated | 1 | 2 | 2 | 1 |
| S3_NK_grid_simultaneous | 10 | 11 | 11 | 10 |
| S3_NK_grid_small_stagger | 6 | 6 | 6 | 6 |
| S3_temporal_ladder_K5 | 5 | 4 | 4 | 5 |
| S3_bounded_spread_largeK | 5 | 5 | 4 | 4 |
| S3_probe_replay | 0 | 0 | 0 | 3 |
| S3_mixed | 2 | 2 | 1 | 1 |
| **total** | **40** | **40** | **38** | **41** |

### Candidate design comparison

| design | configs | sessions | simulated h | frames (M) | descriptors (M) | storage GB | gen+analysis h | GB N20/N50/N100 | longest session |
|---|---|---|---|---|---|---|---|---|---|
| A_minimal | 39 | 195 | 1.39 | 146.1 | 2.91 | 7.7 | 6.0 | 0.2/0.5/6.9 | FCS10_N100_S3_flooding_K005_large (100 vehicles x 60.0 s) |
| B_balanced | 162 | 810 | 5.86 | 445.7 | 8.87 | 23.5 | 18.6 | 2.6/6.7/14.2 | FCS10_N100_S2_malfunction_K000_independent_I05sepa (100 vehicles x 60.0 s) |
| C_comprehensive | 518 | 2590 | 44.69 | 3957.7 | 78.76 | 207.2 | 161.0 | 10.0/43.6/153.6 | FCS10_N100_S3_flooding_K100_large (100 vehicles x 1010.0 s) |


## 8. Scientific coverage

| Research factor | Values represented (baseline b + new) |
|---|---|
| Fleet size N | 2, 5, 10 (b) · 20, 50, 100 |
| Campaign size K | 2, 5, 10 (b) · 2, 5, 10, 20, 50, 100 (K ≤ N) |
| Penetration K/N | 0.02, 0.04, 0.05, 0.1, 0.2, 0.25, 0.4, 0.5, 1 |
| Attacks | flooding / fuzzy / malfunction / replay (frozen parameters) in every configuration family |
| Timing (frozen) | simultaneous; 0.5 s (K ≤ 20); 2 s, 10 s, partial (K = 5 new; K ≤ 10 baseline); 0.1 s replay probe |
| Timing (NEW) | bounded spread 0.5 / 2 / 10 s at K = 20/50/100; S2 staggered 0.5 s and separated 10 s |
| Negatives | S0 (every N), S1 (every N, 4 families), S2 near-simultaneous / staggered / separated (every N) |
| Mixed | coordinated K = 5/10 + 2/5 independent attacks at N = 20/50/100 (b: N = 5/10) |
| Profiles | A/B/C balanced_shuffle; every campaign K_A/K_B/K_C differ by ≤ 1, reported per session |
| Temporal-correlation contrasts | coordinated/simultaneous, coordinated/staggered (0.5, 2, 10 s, bounded), independent/close-time (near-simultaneous, 0.5 s), independent/separated-time (10 s), partial overlap |

## 9. Redundancy analysis (omitted combinations)

| Omitted | Why |
|---|---|
| New N = 2/5/10 sessions | the baseline already covers them; regenerating would duplicate 610 sessions (§4 of the brief). Two optional gap-fills are listed in section 12 for the reviewer to decide. |
| Frozen 2 s / 10 s / partial at K ≥ 10 | spreads 18–990 s; at K = 100 and Δ = 10 s a 1,010-s session of 100 non-overlapping incidents. Temporal ladder is studied at fixed K = 5 across all N (fleet scaling of temporal evidence) and at K = 10 in the baseline (N = 10); large-K spread is studied with the NEW bounded spread. |
| 10 s stagger at K = 100 | as above: the members never overlap, so it would test sequential isolated incidents, not a coordinated campaign; bounded_spread_10s keeps the spread at 10 s for any K. |
| 0.5 s stagger at K = 50/100 | spread 24.5/49.5 s; the per-member 0.5 s step is fully characterised for K ≤ 20 at every N, and large-K temporal spread is covered by bounded spread. |
| Every attack at every timing / N / K | family × timing × N × K interactions are not a research question; rotation keeps each family in every configuration family and all four in the core cells (C would cost 207 GB). |
| K = 2 beyond simultaneous/small/probe | K = 2 is the minimal campaign (pairwise evidence); it is needed as the low-K boundary and lowest ρ (0.02), not across every timing. |
| More S0 configurations | benign variability comes from the 5 seeds (routes, profiles, switch-on, jitter, SUMO); a second S0 configuration at the same N would differ only by seed. |
| S1 at every timing | S1 has no campaign timing; one start (10 s) per family per N suffices for "does more benign context create false correlation around one attacked vehicle". |
| Every mixed combination | mixed composes S2 and S3 conditions already characterised separately; 2 families per N, one (K, m) per N keep it interpretable. |
| Probe at every K / family | the 0.1 s replay probe measures temporal resolution; one per new N checks scale independence. |
| S2 at k = 2 / 20 / 50 | k = 5 and 10 match the coordinated cells used for the matched hard negatives; k = 2 exists in the baseline (N = 2/5). |
| Separated S2 at k = 10 | matched to the coordinated `large` ladder, which is at K = 5 only. |

## 10. Research-question mapping

| Research question | Dataset conditions |
|---|---|
| Fleet scaling (N grows, K fixed) | K = 5: N = 5 (b), 10 (b), 20, 50, 100 (simultaneous, 0.5 s; 2/10 s/partial at 5 (b), 20, 50, 100); K = 2 and 10 analogously; K = 20 at N = 20/50/100 |
| Campaign scaling (K grows, N fixed) | N = 100: K = 2, 5, 10, 20, 50, 100 (simultaneous); N = 50: K = 2 … 50; N = 20: K = 2 … 20; bounded spread at K = 20/50/100 |
| Penetration ρ | iso-ρ diagonals (0.1, 0.2, 0.5, 1) at different absolute K; ρ from 0.02 to 1 |
| Temporal robustness | frozen ladder 0 / 0.5 / 2 / 10 s / partial at K = 5 (all N); bounded spread W = 0.5/2/10 s at large K; 0.1 s probe |
| Negative controls | S0 (false correlation in benign fleets), S1 (single incident), S2 near-simultaneous / staggered / separated with timing matched to coordinated cells, mixed |
| Heterogeneity | A/B/C in every fleet; profile-balanced campaign membership; K_A/K_B/K_C per session; S1 target profile uniform |
| Local evidence | all four families in S1 and in core S3 cells at every N |
| Behavioural vs behaviour + temporal correlation | matched pairs: simultaneous ↔ S2 near-simultaneous; small ↔ S2 staggered 0.5 s; large ↔ S2 separated 10 s (same N, K = k, families) |

## 11. Cost, storage and headroom

* **Design B:** 162 configurations, 810 sessions, 5.86 simulated hours (345 vehicle-hours), ≈ 446 M frames,
  ≈ 8.9 M descriptors, ≈ 23.5 GB, ≈ 18.6 h sequential generation + analysis (one session at a time, as measured).
  Per N: 2.6 / 6.7 / 14.2 GB and 2.2 / 5.3 / 11.1 h for N = 20 / 50 / 100. Per scenario: S0 0.57, S1 1.14,
  S2 5.02, S3 15.65, mixed 0.77, probe 0.29 GB. Raw frames vs descriptors/GT/metadata/validation: the baseline
  splits 1.9 GB : 0.58 GB (≈ 76 % : 24 %) → ≈ 17.9 GB raw and ≈ 5.6 GB derived for B.
* Largest session ≈ 101 MB (N = 100, 60 s); temporary storage per session ≤ 0.2 GB; peak RAM ≤ ≈ 1.4 GB
  (measured 782 MB for 40 s and 883 MB for 80 s benign at N = 100; 1,336 MB for the 100-member replay campaign).
* **Disk headroom (precondition for generation, not for this design):** root LV 50.5 GB with **20.1 GB free**
  (`df`, all sizes here decimal GB = 10⁹ bytes), less than B's ≈ 23.5 GB (+ ≈ 7 % model margin). The volume group
  already contains ≈ 10.4 GiB ≈ 11.2 GB unallocated space (`lsblk`: partition vda3 60.9 GiB vs LV 50.5 GiB). Extending the LV by that space
  (an interactive `sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv`, to be run by you only after
  approval) gives ≈ 31 GB free and ≈ 7.5 GB headroom after B. Without the extension only phase 1 (9.9 GB) fits with
  margin; phase 2 would require moving finished phase-1 data off the VM. No system change was made in M9.

## 12. Optional additions for the reviewer (NOT in the proposed YAML)

* **G0 route-source bridge (N = 10, grid_loops_v1):** N ≤ 10 (frozen SUMO scenario) and N ≥ 20 (generated
  grid_loops_v1 routes) differ in road network/route source as well as size, which confounds N with route source
  in cross-component comparisons. A small bridge — N = 10 with grid_loops_v1 (fleet `config/m8/fleets/fleet_10.yaml`):
  S0 + S3 K = 5 simultaneous and small × 2 families — would measure that effect directly
  (5 configurations, 25 sessions, ≈ 0.2 GB).
* **G1 K = 5 ladder gap at N = 10:** the baseline has 2 s / 10 s / partial at N = 10 only for K = 10, so the fixed-K
  fleet-scaling series for those timings has N = 5, 20, 50, 100 but not N = 10. Filling it (3 patterns × 2 families,
  6 configurations, 30 sessions, ≈ 0.2 GB) is a justified missing condition in the sense of §4 of the brief.

Both are small and would be added to the matrix only on your decision.

## 13. Provenance per session and versioning proposal

Each future session must record (metadata/tags, as M6 did): dataset component, generator version (1.3.0),
schema 1.0, descriptor 1.0, Git commit, configuration ID, seed, N, K, scenario, attack, temporal pattern
(frozen or NEW name), independent timing, K_A/K_B/K_C, member order, phase. The YAML already carries every
per-configuration field; the generator already records versions, Git commit, seed, scenario and fleet profiles.

Versioning (recommended, **not adopted**):

1. **fleetcan-sim-1.1** stays immutable and is referenced only by hash (SHA256SUMS `bc523937…`, FREEZE `df58000a…`).
2. The new sessions form a separately frozen component **fleetcan-sim-scale-1.0** (session prefix `FCS10_`, seeds
   920001–921615, generator 1.3.0) with its own manifest, SHA256SUMS and FREEZE.json under
   `data/final/fleetcan-sim-scale-1.0/`.
3. Optionally, an umbrella release **fleetcan-sim-2.0** = manifest listing both components by hash (no files copied,
   moved or renamed), created only after both are frozen and you approve. Analyses over N = 2 … 100 must keep the
   component (generator version, route source) as an explicit covariate rather than pretend a single generation.

## 14. Validator (`scripts/validate_m9_matrix.py`, read-only)

| Check | Content | Result |
|---|---|---|
| M01 | `approved_for_generation: false`; no runner/script outside M9 references the matrix | PASS |
| M02 | schema; config_id and duration recomputed from the fields | PASS (162) |
| M03 | valid scenario, attack family, temporal pattern per scenario | PASS |
| M04 | K in ladder, 2 ≤ K ≤ N; K + mixed independents ≤ N; S2 k ≥ 2 | PASS |
| M05 | repetitions = 5 = seeds = session IDs | PASS |
| M06 | unique config / session IDs, `<config_id>_R<rep>`, prefix `FCS10_` | PASS (810 distinct) |
| M07 | unique seeds following the declared formula | PASS (920001–921615) |
| M08 | no two configurations with the same parameter tuple | PASS |
| M09 | no collision with fleetcan-sim-1.1 (manifest, matrix spec) or any of the 899 existing sessions / 668 seeds | PASS |
| M10 | delay vectors reproduce the declared pattern; S2/mixed starts; warm-up; duration ≥ last member end + 5 s | PASS |
| M11 | fleet config exists; frozen profile assignment gives A/B/C counts differing ≤ 1 for every seed | PASS (810) |
| M12 | member selection resolves deterministically (twice), counts, disjointness, K_A/K_B/K_C spread ≤ 1, equal to the CSV | PASS (810) |
| M13 | the campaigns each session would use pass the frozen scheduler (`build_schedule`) and scenario definition (`check_scenario`); members end in time | PASS (810) |
| M14 | every family present in every configuration family (except the replay probe) | PASS |

Negative tests (`tests/test_m9.py`) confirm that each of approved = true, unknown attack, K > N, wrong repetitions,
duplicate session ID, duplicate seed, duplicate configuration, baseline seed, baseline session ID, too-short duration
and a wrong delay is detected. Full suite: 264 passed. Baseline integrity re-verified after M9.

Reproduce:

```
.venv/bin/python scripts/m9_design.py --calibrate
.venv/bin/python scripts/m9_design.py --write
.venv/bin/python scripts/validate_m9_matrix.py --report results/m9_design/validation_report.json
.venv/bin/python -m pytest -q tests
```

## 15. Answers

**Q1 — fleet sizes.** N = 2, 5, 10 (fleetcan-sim-1.1, reused unchanged) + N = 20, 50, 100 (new component).

**Q2 — campaign sizes per N.** Baseline: N = 2: 2; N = 5: 2, 5; N = 10: 2, 5, 10. New: N = 20: 2, 5, 10, 20;
N = 50: 2, 5, 10, 20, 50; N = 100: 2, 5, 10, 20, 50, 100 — the full nested grid, but only crossed completely with
the simultaneous anchor.

**Q3 — timing per N/K.** Simultaneous at every (N, K); frozen 0.5 s for K ≤ 20 at every N; frozen 2 s / 10 s /
partial at K = 5 for every new N (baseline: K = N ≤ 10); NEW bounded spread 0.5 / 2 / 10 s for K ∈ {20, 50, 100};
0.1 s replay probe at K = 2 for every N; mixed with the frozen 0.5 s pattern.

**Q4 — scenarios.** S0: one 40-s benign configuration per new N. S1: one seeded vehicle, all 4 families, every new N.
S2: k = 5 and 10 independent same-family attacks (`coordinated: false`, separate campaign IDs) near-simultaneous
(frozen), staggered 0.5 s and separated 10 s (NEW, timing-matched to coordinated cells). S3: N × K grid, 0.5 s grid,
K = 5 ladder, bounded spread, probe. Mixed: coordinated K = 5/10 + 2/5 independent attacks at every new N.

**Q5 — attack families.** Controlled reduced factorial: all four in the core cells (S1, S2 k = 5, S3 K = 5 and
K = N at every N), deterministic balanced rotation of two families (one for bounded spread) elsewhere; every family
present in every configuration family; 38–41 configurations per family.

**Q6 — repetitions.** R = 5 for every configuration (baseline consistency; no reduction proposed).

**Q7 — new configurations and sessions.** 162 configurations × 5 = **810 sessions** (optional G0/G1: +11 / +55).

**Q8 — cost.** ≈ 446 M frames, ≈ 8.9 M descriptors, ≈ 23.5 GB (≈ 17.9 GB raw + ≈ 5.6 GB GT/descriptors/metadata),
≈ 18.6 h sequential generation + analysis, 5.86 simulated hours; within the measured range (calibration error
frames ≤ 2 %, storage ≤ 7 %, time ≤ 10 %). Requires the LV extension (≈ 11.2 GB already in the VG) or phased
off-VM storage before generation.

**Q9 — N and K separable?** Yes: iso-K rows (K = 2, 5, 10 over six N; K = 20 over three), iso-N columns (N = 50,
100 over all K), and iso-ρ diagonals (ρ = 0.1, 0.2, 0.5, 1 at different absolute K) are all present with 5
repetitions each. Caveat: N ≤ 10 vs N ≥ 20 also differ in route source and generator version (bit-identical
semantics, different road network) — optional bridge G0 measures this.

**Q10 — temporal-correlation and ROBUST-FLEET-GUARD readiness without modifying the base dataset?** Yes. All five
required contrasts exist (coordinated simultaneous / staggered, independent close-time / separated-time, partial
overlap), with matched coordinated/independent timing, and must be analysed from observable timestamps (configured
times are GT only). Campaigns of K = 20–100 in fleets of 50–100 provide the scale needed later for compromised
member subsets, descriptor perturbation, topology fragmentation, false edges, suppression and false merging — to be
applied as post-processing on copies in a later stage; the base data contain no adversarial manipulation, and no
detector, threshold or graph parameter was chosen here.

## Verdict

**DESIGN READY FOR REVIEW** — the proposed matrix is scientifically justified and technically feasible; generation
has NOT started. Open decisions for you: approve/modify design B, decide the optional G0/G1 additions, the
component naming, and the disk extension before any generation.
