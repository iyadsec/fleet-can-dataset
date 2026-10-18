# Final dataset-generation protocol (FROZEN at Milestone 5.1: generator 1.1.0, schema 1.0, descriptor 1.0)

Status: **frozen, not generated**. Final dataset_version: `fleetcan-sim-1.1`. Frozen fleet files: `config/final/fleets/fleet_{2,5,10}.yaml`. Every number below comes from Milestone 3–5 measurements
(scripts in `scripts/m5_*.py`); see the Milestone 5 report.

## 1. Fleet

| Item | Setting | Basis |
|---|---|---|
| Fleet sizes | 2, 5, 10 | M3 scaling, original FLEET-GUARD campaign sizes |
| Profiles | A / B / C (`config/profiles/profiles_abc.yaml`), seeded `balanced_shuffle`: N >= 3 balanced counts (differ by <= 1) in seeded order; N < 3 seeded subset without replacement (N=2 reaches A+B, A+C, B+C) | §2, M5.1 |
| Switch-on offset | per vehicle, seeded, uniform **[0, 1.0] s**, 1 µs resolution | phase sensitivity: 0–1.0 s gives grid misalignment ≈ ideal Uniform(0, stride/2) |
| ECU release jitter | per (vehicle, message) seeded stream, release = nominal + U(−1 %, +1 %) × period (±0.1 ms @10 ms, ±0.2 ms @20 ms, ±0.5 ms @50 ms); no accumulation, nominal period preserved | M5.1 jitter sensitivity |
| Bus | 500 kbit/s logical model, exact stuffing, queue 32/node, drop_newest, priority order, **deadline expiry 100 ms** | M2/M3 |
| Execution | fast mode (logically identical to real time, M5 equivalence) | §6 |
| Storage | Parquet (zstd); CSV only for examples/debugging | §6 |

## 2. Profiles (synthetic, not OEM) — what differs and why

| | Profile A | Profile B | Profile C |
|---|---|---|---|
| DBC | `generic_vehicle.dbc` | `generic_vehicle_b.dbc` | `generic_vehicle_c.dbc` |
| Messages / IDs | 5: 0x100, 0x120, 0x150, 0x180, 0x200 | 4: 0x0C1, 0x0D0, 0x1A0, 0x2B0 | 5: 0x101, 0x130, 0x131, 0x190, 0x200 |
| Frames/s | 350 | 270 | 350 |
| Periods (ms) | 10, 20, 20, 20, 10 | 20 (engine), 10 (steer), 10 (speed), 50 (pedal+brake) | 10, 20, 20, 20, 10 |
| Phases (ms) | 0 / 1 / 3 / 5 / 7 | 2 / 4 / 6 / 8 | 0.5 / 2.5 / 4.5 / 6.5 / 8.5 |
| DLC | 8 | 8 | 6, 8, 6, 4, 5 |
| Packing | one message per function | pedal + brake share one message | throttle inside engine message; separate wheel-speed message |
| Scaling | speed 0.01 km/h, RPM 0.25 | speed 0.05 km/h, RPM 1 | speed 0.01 km/h, RPM 0.125 |
| Counters | 4-bit `Counter` | 4-bit `AliveCounter` | 4-bit `Counter` (none in WHEEL_SPEEDS) |
| Message set | base | no separate brake message | + per-wheel speeds (synthetic: equal to vehicle speed) |

Common to all: the SUMO mobility state, the synthetic physical model (gearbox, pedal, brake,
steering), bus settings, attack definitions. Profiles were not tuned for detectability.
Semantic roles (`vehicle_speed`, `engine_rpm`, `accelerator`, `brake`, `steering`) map attack targets
to each profile's own message/signal, so a campaign shares intent, not bytes or CAN IDs.

## 3. Attacks (one canonical parameter set per family; not tuned on detector results)

| Family | Parameters | Cross-profile semantics |
|---|---|---|
| Flooding | ID 0x000, 1600 frames/s (~41 % bus load), 8 zero bytes, 5 s | profile-independent |
| Fuzzy | random ID 0x000–0x7FF + random payload/DLC, 1000 frames/s, 5 s | profile-independent |
| Malfunction | inject, target `vehicle_speed` = 250 km/h, 100 frames/s, 5 s (next legitimate counter) | role resolved per profile |
| Replay | own bus, window [start−5 s, start), +0.25 ms, duration 5 s | replays the vehicle's own profile traffic |

## 4. Scenarios and temporal patterns

Delays come from the measured independent-phase resolution: first majority-malicious descriptor
|error| p95 ≈ 0.11 s, max 0.12 s → delays differing by > ~0.24 s are separable; 0.1 s is not
separable from 0 s.

| Pattern | Member start delays | Use |
|---|---|---|
| simultaneous | 0 | same time, same campaign |
| small_stagger | 0.5 s steps | smallest reliably separable step |
| medium_stagger | 2 s steps | propagation |
| large_stagger | 10 s steps | slow propagation |
| partial_overlap | 5 s steps, 10 s attacks (50 % pairwise overlap) | overlap without common start |
| sub_resolution (probe) | 0.1 s | stress test only (replay, size 2) |

Per fleet size N (one repetition):

| Scenario | Configurations |
|---|---|
| S0 | 1 benign |
| S1 | 4 (one isolated attack per family, one vehicle) |
| S2 | 4 (per family: 2 / 3 / 5 independent single-vehicle campaigns for N = 2 / 5 / 10, starts 0.0–0.5 s apart — hard negatives) |
| S3 | per family: campaign size N × 5 patterns; plus sizes 2 and 5 (< N) × {simultaneous, small_stagger} |
| S3-mixed (N ≥ 5) | 4 (coordinated size 2 / 5 + 1 / 2 independent same-family attacks at similar times) |
| S3-probe | 1 (replay, size 2, 0.1 s) |

Totals: N=2: 30, N=5: 42, N=10: 50 → **122 configurations × 5 seeds = 610 runs**.
Seeds: one experiment seed per run drives SUMO, profile assignment, switch-on, campaign and attack RNGs
(sub-seeds derived and recorded). Durations: 10 s warm-up (switch-on + benign calibration period,
attacks never before 10 s), attacks, 5 s tail.

## 4b. Seed derivation (independent random processes)

| Random process | Seed | Changing it affects only |
|---|---|---|
| SUMO driver imperfection | experiment seed (`--seed`) | mobility |
| Profile assignment | `derive_seed(seed, "profile_assignment")` (or explicit) | which profile each vehicle gets |
| Switch-on offsets | `derive_seed(seed, "switch_on")`, one draw per vehicle in vehicle order | ECU start phase (adding vehicles appends draws; existing vehicles keep theirs) |
| ECU release jitter | `derive_seed(derive_seed(seed, "ecu_jitter"), "ecu_jitter/<vehicle>/<message>")` | that one message stream |
| Campaign delays (uniform_random) | `SeedSequence([seed, crc32("campaign:" + campaign_id)])` | that campaign |
| Attack randomness (fuzzy IDs/payloads, random payloads) | `SeedSequence([seed, crc32(attack_id)])` | that attack |

`derive_seed(seed, tag) = SeedSequence([seed, crc32(tag)]).generate_state(1)[0]`. Every sub-seed is recorded
in metadata. Adding, removing or changing one attack/campaign/vehicle stream does not change any other stream.

## 5. Estimated size (measured rates)

~5.0 h simulated, ~50 M frames, ~2.7 GB Parquet (incl. descriptors; CSV would be ~23 GB),
~1.9 h sequential fast-mode wall time (`scripts/m5_matrix_estimate.py`).

## 6. Storage, execution, validation

* Parquet and CSV are logically identical (all tables equal; descriptors equal up to CSV float parsing, ≤3e−14).
* Fast and real-time mode are logically identical (all tables and descriptors bit-equal); capture times differ.
* Every run must pass generator validation (V/B/A/C checks) and descriptor validation (D01–D07).

## 6b. Storage location (decision before generation)

Final generation needs ~2.7 GB (measured rate) plus headroom; the VM root volume has ~5.0 GB free.
The VM disk (`vda`, 64 GB virtual) holds a 60.9 GB LVM physical volume of which the root logical volume uses
30.5 GB, i.e. ~30 GB is unallocated inside the existing volume group. Recommended (safest): extend the root
LV online inside the VM (`sudo vgs` to confirm free space, then `sudo lvextend -r -L +20G /dev/ubuntu-vg/ubuntu-lv`);
no VM shutdown, no change to the UTM image or partition table, no data moved. Do not generate before this is done.

## 7. Manifest and versions

`scripts/build_manifest.py` writes `data/manifest.jsonl` (one object per session) with: session/experiment
IDs, dataset/schema/generator/descriptor versions, scenario, tags, fleet size, vehicle IDs, profiles,
assignment strategy and seed, switch-on offsets, attack types and IDs, campaign IDs, sizes, coordination,
delay patterns and configured relative delays, seed, durations, execution mode, wall time, frame and drop
counts, storage format, raw / ground-truth / descriptor locations, descriptor count, git commit and dirty
flag, generator and descriptor validation status, bytes on disk.

Versions (`src/version.py`): `schema_version` 1.0 (tables), `generator_version` 1.1.0 (1.0.0 + N<3 profile subset + ECU jitter),
`descriptor_version` 1.0; `dataset_version` set per experiment config (`fleetcan-sim-1.1` for the final
dataset, `-pilot` / `-dev` otherwise). The manifest also records the ECU jitter setting per session. Validator V27 refuses sessions with another schema version.
