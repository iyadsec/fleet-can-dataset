## Matrix table (recommended design B, one row per configuration family x N)

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

## N x K coverage of coordinated campaigns (configurations; baseline cells marked b)

| N \ K | 2 | 5 | 10 | 20 | 50 | 100 |
|---|---|---|---|---|---|---|
| 2 | b21 (large:4, medium:4, partial:4, probe:1, simultaneous:4, small:4) | - | - | - | - | - |
| 5 | b13 (probe:1, simultaneous:4, small:8) | b20 (large:4, medium:4, partial:4, simultaneous:4, small:4) | - | - | - | - |
| 10 | b9 (probe:1, simultaneous:4, small:4) | b12 (simultaneous:4, small:8) | b20 (large:4, medium:4, partial:4, simultaneous:4, small:4) | - | - | - |
| 20 | 5 (probe:1, simultaneous:2, small:2) | 14 (large:2, medium:2, partial:2, simultaneous:4, small:4) | 4 (simultaneous:2, small:2) | 9 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:4, small:2) | - | - |
| 50 | 5 (probe:1, simultaneous:2, small:2) | 12 (large:2, medium:2, partial:2, simultaneous:4, small:2) | 6 (simultaneous:2, small:4) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:2, small:2) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:4) | - |
| 100 | 5 (probe:1, simultaneous:2, small:2) | 12 (large:2, medium:2, partial:2, simultaneous:4, small:2) | 6 (simultaneous:2, small:4) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:2, small:2) | 5 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:2) | 7 (bounded_spread_0p5s:1, bounded_spread_10s:1, bounded_spread_2s:1, simultaneous:4) |

## Penetration rho = K / N represented (coordinated campaigns)

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

## Attack family x configuration family (configurations)

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

## Candidate design comparison

| design | configs | sessions | simulated h | frames (M) | descriptors (M) | storage GB | gen+analysis h | GB N20/N50/N100 | longest session |
|---|---|---|---|---|---|---|---|---|---|
| A_minimal | 39 | 195 | 1.39 | 146.1 | 2.91 | 7.7 | 6.0 | 0.2/0.5/6.9 | FCS10_N100_S3_flooding_K005_large (100 vehicles x 60.0 s) |
| B_balanced | 162 | 810 | 5.86 | 445.7 | 8.87 | 23.5 | 18.6 | 2.6/6.7/14.2 | FCS10_N100_S2_malfunction_K000_independent_I05sepa (100 vehicles x 60.0 s) |
| C_comprehensive | 518 | 2590 | 44.69 | 3957.7 | 78.76 | 207.2 | 161.0 | 10.0/43.6/153.6 | FCS10_N100_S3_flooding_K100_large (100 vehicles x 1010.0 s) |
