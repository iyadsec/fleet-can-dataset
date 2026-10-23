# Scenarios, campaigns and timing patterns (frozen matrix `fleetcan-sim-scale-1.0-matrix-v1`)

167 configurations × 5 repetitions = 835 sessions; matrix SHA-256
`9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735`. Attacks start at ≥ 10 s (benign warm-up).

| Scenario | Content | Campaign table |
|---|---|---|
| S0 | benign only (N = 10 (G0), 20, 50, 100; 40 s) | none |
| S1 | one isolated attack on one seeded vehicle (10–15 s) | one non-coordinated campaign |
| S2 | k independent single-target attacks of one family (hard negatives); timing near-simultaneous (starts over [10, 10.5] s), staggered (10 + 0.5 j s) or separated (10 + 10 j s) | k campaigns, `coordinated = false`, distinct IDs |
| S3 | one coordinated campaign with K members | one campaign, `coordinated = true` |
| S3-mixed | coordinated `small` campaign (K = 5/10) + m = 2/5 independent same-family attacks at 10.25 + 0.5 j s | 1 coordinated + m independent |
| S3-probe | replay, K = 2, member delays [0, 0.1] s (0.1 s probe) | one coordinated campaign |

Coordinated timing patterns (member i, i = 0…K−1):

| Pattern | Member start | Attack duration |
|---|---|---|
| simultaneous | 10 s | 5 s |
| small / medium / large | 10 + 0.5 i / 2 i / 10 i s | 5 s |
| partial | 10 + 5 i s (consecutive members overlap exactly 5 s) | 10 s |
| bounded_spread_W (W = 0.5 / 2 / 10 s, K ≥ 20) | 10 + W·i/(K−1) s | 5 s |
| probe | 10, 10.1 s | 5 s |

Members: seeded, profile-balanced selection (`seeded_profile_balanced_v1`) for N ≥ 20; first K vehicles
(`baseline_first_k`) in G0. Membership is configured, never inferred from timing.

Independent attacks (S2, S3-mixed independents, S1) are stored as separate campaigns with `coordinated = false`;
they are kept distinct from coordinated campaigns in every table (`results/campaign_instances.csv`,
column `coordinated`).

Per-campaign configured vs observable onset spread, order agreement and missing evidence:
`results/campaign_instances.csv`, `results/campaign_timing.csv`, `reports/P1_temporal_validation.md`.
