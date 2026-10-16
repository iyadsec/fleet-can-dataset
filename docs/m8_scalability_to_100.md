# Milestone 8 — dataset generator scalability to 100 vehicles

**Result: N = 100 PASS for the tested session lengths (≤ 40 s at N = 100; ≈ 1.3 M frames per session); memory is the
binding limit for longer 100-vehicle sessions.** Scientific generation semantics unchanged; fleetcan-sim-1.1 untouched.

## 1. Objective and baseline

Extend the generator from N = 2/5/10 to 20/50/100 simultaneous vehicles without changing generation semantics.
Baseline: fleetcan-sim-1.1 (610 sessions, generator 1.1.0, commit 1131b27). Architecture audit written first:
`docs/m8_scalability_architecture_audit.md` (commit acdb294).

## 2. Changes (generator 1.1.0 → 1.2.0)

| File | Old behaviour | New behaviour | Why | Semantics affected? |
|---|---|---|---|---|
| `src/route_generator.py` (new) | — | `grid_loops_v1`: seeded routes for any N on the existing grid (same loops, vType, depart t = 0, speed 0); 30 m start slots drawn without replacement (168 slots); route length ≥ 2× speed × duration; validity checks | static routes existed only for N = 2/3/5/10 | no (new opt-in path; legacy routes unchanged) |
| `src/config.py` | `simulation.sumo_config` required | exactly one of `sumo_config` (legacy) or `route_generation` (new); `ExperimentConfig.route_generation` | select the new path explicitly | no |
| `src/orchestrator.py` | uses the static sumocfg | if `route_generation`: write `data/generated_sumo/<session>/{scenarios,routes,network}` with route seed `derive_seed(seed, "route_generation")`, then run as before; record provenance | generated scenario | no (legacy branch identical) |
| `src/metadata.py` | `sumo_config` path relative to repo | same; adds `simulation.route_generation` (method, seed, slots, per-vehicle loop/edge/position, route & network SHA-256) only for generated runs | provenance | no |
| `src/version.py` | generator 1.1.0 | generator 1.2.0 | infrastructure added | no |
| `scripts/setup_vcan.sh` | refuses N > 64 | refuses N > `MAX_VCAN` (default 128) | 64 was an M1 typo guard, not a kernel limit | no |
| `config/m8/` (new) | — | fleets N = 10/20/50/100 with frozen settings and `vehicle_{n:03d}` IDs; 15 engineering test experiments | tests | no (IDs only in new configs; frozen configs keep `vehicle_{n:02d}`) |
| `scripts/m8_scaling.py` (new) | — | instrumented runner (/proc sampling of RSS, threads, fds, sockets, CPU), verifier, comparisons | measurement | no |
| `tests/test_m8.py` (new), `tests/test_m51.py` | M5.1 test pinned generator "1.1.0" | accepts 1.1.0/1.2.0 (its logical equivalence assertions unchanged) | version bump | no |

Unchanged: SUMO clock and 10 ms step, profiles A/B/C (IDs, messages, periods, packing, scaling), switch-on, ±1 % jitter,
all RNG derivations, CAN model (500 kbit/s, exact stuffing/CRC, arbitration, queue 32, drop-newest, 100 ms deadline),
the four attacks, campaigns, ground-truth schema, frame UIDs, 24-D descriptor (100/50), timestamps, Parquet/ZSTD.
Capture threading and in-memory frame retention were measured and **kept** (no redesign needed for the tested cases).

## 3. SUMO routing

Existing 4×4 network (48 single-lane edges ≈ 187 m, 8,973 lane-m, 12 traffic lights) kept: 100 cars occupy ≈ 8 % of
lane length standing, ≈ 24 % moving — no redesign. Every vehicle was present from the first SUMO step and for the whole
run in every N = 20/50/100 session. Loop occupancy (P/R/I): N = 20 9/4/7, N = 50 20/23/7, N = 100 42/43/15 (S0 seeds).
Route files are reproducible from the seed (verifier regenerates and compares SHA-256). Limitation: more vehicles on the
same network means denser traffic (more stops at lights) — an inherent property of N, not a model change.

## 4. Infrastructure

* vCAN: `sudo scripts/setup_vcan.sh 100` → vcan0–vcan99 up (idempotent; `cleanup_vcan.sh all` removes them; non-persistent).
  Creation time not isolated (the 7 min 15 s wall time included the interactive password prompt).
* One logical CAN bus per vehicle: vehicle_i ↔ `vcan(i−1)` ↔ its own `CanBusModel`; verified for every session
  (distinct channels, no frame on a foreign channel). Fleet correlation can only happen above the per-vehicle buses.
* Sockets / fds: 2 sockets per vehicle (capture + ECU sender), +1 per attacked vehicle (attacker sender).
  fd peak = 2N + 9 benign (209 at N = 100), 309 with 100 attacked vehicles; default `ulimit -n` 1024 is sufficient — no OS change.
* Threads: N capture threads + 9 (109 at N = 100). 0 capture loss and 0 flow-control timeouts in every run → retained.
* CPU: single orchestrator process + SUMO; mean 114–134 %, peak ≈ 400 % (Parquet/validation phases).

## 5. Correctness per N

| N | SUMO | CAN | Capture | GT | Descriptors | Determinism | Result |
|---|---|---|---|---|---|---|---|
| 10 | all present; 10 ms | jitter re-derived; bit timing ok | 0 loss | PASS | recon. err 7.3e-16 | regression vs frozen: 6/6 identical | PASS |
| 20 | 20/20 from step 1 | as above; 0 overlaps | 0 loss | PASS (20-member campaign, 0.5 s steps) | 9.8e-16 | repeat identical | PASS |
| 50 | 50/50 | as above | 0 loss | PASS (10-member, 2 s; 10 independent) | 8.7e-16 | repeat identical | PASS |
| 100 | 100/100 | as above; vehicle_100 on vcan99 | 0 loss | PASS (100-member simultaneous; 2-member 10 s; A/B/C attacks) | 6.9e-16 | repeat identical (1,277,152 frames) | PASS |

Every session: generator validation PASS, descriptor validation PASS, profiles/switch-on/jitter/routes re-derived from the
seed with 0 mismatches, frame UIDs unique, labels 1:1, descriptors per vehicle = ⌊(n−100)/50⌋+1, timestamp = window end.

## 6. Attacks, campaigns, independence, isolation at scale

* N = 100, each family on one Profile A, B and C vehicle (vehicle_002/001/004) as independent campaigns
  (`coordinated=false`): all PASS (A04–A07, C-checks).
* Coordinated: N = 20 size 20 (0.5 s steps), N = 50 size 10 (2 s), N = 100 size 100 (0 s), N = 100 size 2
  (vehicle_001 + vehicle_100, 10 s) — member lists, attack IDs and relative delays exact (C03 PASS).
* Independent at scale: N = 50, 10 single-vehicle replay campaigns, starts 10.0–10.5 s, all `coordinated=false`, distinct IDs.
* Isolation: N = 20, same seed with and without an over-saturating flood on vehicle_001 (5,000 fps → 5,283 queue drops,
  1,720 expired, all accounted): the other **19 vehicles' frames and descriptors are bit-identical** to the benign run.
* Test resizing (disclosed): the first plan had N = 50 size 25 @ 2 s (68 s), N = 100 size 100 flooding and N = 100 size 10 @ 10 s
  (110 s); these exceed the measured memory budget (§8) and were replaced before running by the sizes above. Engineering tests only.

## 7. N = 10 regression

Generator 1.2.0 re-ran six frozen fleetcan-sim-1.1 configurations (N10 S0, N10 S3 replay large, N05 mixed malfunction,
N02 replay probe, N10 S2 fuzzy, N10 S3 flooding partial) under new session IDs (`M8_REG_*`, dataset label
`m8-regression-test`): **all tables, SUMO states, ground truth and descriptors identical** to the frozen sessions; profile,
switch-on, jitter seeds, campaign schedule and attack runtime metadata identical.

## 8. Resources (benign S0, 40 s, same machine/settings)

| N | wall s | RTF | CPU mean % | peak RSS MB | fds | threads | frames | descriptors | MB |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 18.9 | 2.12 | 114 | 769 | 29 | 19 | 128,667 | 2,559 | 6.2 |
| 20 | 32.3 | 1.24 | 124 | 1,331 | 49 | 29 | 254,248 | 5,053 | 10.6 |
| 50 | 77.8 | 0.51 | 130 | 2,942 | 109 | 59 | 637,046 | 12,664 | 24.6 |
| 100 | 155.3 | 0.26 | 134 | 5,564 (repeat 5,926) | 209 | 109 | 1,277,152 | 25,397 | 47.9 |

Ratios to N = 10 at N = 100: frames 9.93, descriptors 9.92, sockets 9.18 (linear); wall 8.21, memory 7.23, storage 7.72
(slightly sub-linear: fixed overhead amortised; ≈ 1.55 s wall per vehicle per 40 s at large N); CPU 1.17 (single process).
Per vehicle: ≈ 12,750 frames, 254 descriptors, 0.48 MB per 40 s.
Memory: the simulation loop retains ≈ 1.5 KB/frame; the peak (≈ 4.5 KB/frame) is the end-of-run record + in-process
validation phase. Lowest MemAvailable during all N = 100 runs: 1.05 GB of 7.9 GB, no swap growth, watchdog never fired.
**Budget on this VM ≈ 1.3 M frames per session** (≈ 3,700 vehicle-seconds of benign traffic, e.g. 100 vehicles × 37–40 s).

## 9. Limitations / bottlenecks

* Memory O(N × duration) per session: a 100-vehicle session longer than ≈ 40 s (e.g. 10 s-stagger campaigns of size > 2)
  would exceed the 7.9 GB VM. Engineering options for review (not implemented): columnar/streamed record phase,
  validation in a separate process, more VM RAM. Any of them must be proven equivalent (compare_sessions) before use.
* Speed: RTF 0.26 at N = 100 (fast mode only; real-time mode not possible beyond ≈ 25 vehicles (RTF 1.24 at N = 20, 0.51 at N = 50)).
* `vehicle_{n:02d}` would still work at N ≥ 100 but sorts non-numerically; new configs use `vehicle_{n:03d}`.
* vCAN interfaces need sudo and are not persistent across reboots.
* Denser traffic at large N on the same 4×4 network (mobility conditions depend on N).

## 10. Reproduction

```
cd ~/fleet-can-dataset
sudo scripts/setup_vcan.sh 100
.venv/bin/python scripts/m8_scaling.py configs
.venv/bin/python scripts/m8_scaling.py run M8_S0_N010 M8_S0_N020 M8_S0_N050 M8_S0_N100
.venv/bin/python scripts/m8_scaling.py run M8_S0_N020 M8_S0_N050 M8_S0_N100 --suffix _REPEAT
.venv/bin/python scripts/m8_scaling.py run M8_ATK_N100_flooding_ABC M8_ATK_N100_fuzzy_ABC M8_ATK_N100_malfunction_ABC M8_ATK_N100_replay_ABC M8_CAMP_N020_replay_s20_small M8_CAMP_N050_malfunction_s10_medium M8_CAMP_N100_replay_s100_simultaneous M8_CAMP_N100_fuzzy_s02_large M8_S2_N050_replay_10ind M8_ISO_N020_S0 M8_ISO_N020_FLOOD_OVERSAT
.venv/bin/python scripts/m8_scaling.py regress FC11_N10_S0_none_none_none_R1 FC11_N10_S3_replay_s10_large_R4 FC11_N05_S3-mixed_malfunction_s02_small_R3 FC11_N02_S3-probe_replay_s02_probe_R1 FC11_N10_S2_fuzzy_s05_independent_R2 FC11_N10_S3_flooding_s10_partial_R1
.venv/bin/python scripts/m8_scaling.py verify <session>;  .venv/bin/python scripts/m8_scaling.py compare <a> <b>;  .venv/bin/python scripts/m8_scaling.py interference M8_ISO_N020_S0 M8_ISO_N020_FLOOD_OVERSAT
.venv/bin/python -m pytest -q tests
cd data && sha256sum -c --quiet final/fleetcan-sim-1.1/SHA256SUMS
```
Results: `results/m8_scalability/` (`scalability_metrics.csv`, `resource_scaling_S0.csv`, `verify/`, `compare/`, `logs/`).
Test sessions live under `data/` as `M8_*` (dataset_version `m8-scalability-test` / `m8-regression-test`), never under `data/final/`.
