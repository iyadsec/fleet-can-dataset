# Final Dataset Requirements Audit — fleetcan-sim-1.1

Date: 2026-10-16. Scope: READ-ONLY. Nothing under `data/` was written, regenerated or repaired
(SHA-256 verification of all 20,805 frozen files passed before and after the audit).
Milestone 7 detection results were **not** used to judge any requirement.

**Verdict: B — DATASET VALID WITH DOCUMENTED LIMITATIONS** (no critical or major finding; no regeneration).

Automated evidence: `scripts/final_requirements_audit.py` → `results/final_requirements_audit/`
(`audit_results.json`, `per_session_audit.csv`, coverage CSVs). The script re-implements the checks
independently of the generator where practical (matrix from the protocol table, seed/assignment/switch-on/jitter
re-derivation, CRC-15 + bit stuffing, cantools decode, replay timing, descriptor reconstruction).

## 1. Authoritative requirement sources (all pre-generation)

| Source | Where | Date / commit |
|---|---|---|
| R-PLAN: original testbed plan (architecture, clock, CAN, heterogeneity, GT separation, attacks, campaigns A–F, S0–S4, temporal capability, timing realism, validation, integrity) | user specification, 2026-10-15 17:17 UTC | before 4ae40c4 |
| R-M2: bus timing/arbitration model, four attacks, three time bases | user specification, 2026-10-15 17:50 | before a9d4cd4 |
| R-M3: frame expiry, campaign abstraction, simultaneous / stagger / partial overlap / independent / mixed, timing-layer separation, 2/5/10 scaling | user specification, 2026-10-15 18:23 | before ef7ff38 |
| R-M5: A/B/C heterogeneity, seeded assignment, switch-on, Parquet, fast mode, final matrix, campaign sizes 2/5/10, hard negatives, manifest, versions | user specification, 2026-10-15 20:13 | before 3cdc1f6 |
| R-M51: N<3 subset, ±jitter (seeded, no accumulation), saturation equivalence, freeze, seed independence | user specification, 2026-10-16 07:09 | before b0a75b1 |
| R-M6: frozen values (fleet, CAN, attacks, patterns, campaign sizes, 5 seeds, descriptors), validation, completeness, hashes, provenance, freeze | user specification, 2026-10-16 07:31 | before d4ef0c2 |
| Frozen protocol | `docs/final_protocol.md` (snapshot in `data/final/fleetcan-sim-1.1/provenance/`) | 3e732aa |
| Frozen configuration | `config/profiles/profiles_abc.yaml`, `config/final/fleets/`, `config/final/matrix/` (+ provenance copies), `dbc/*.dbc` | 3e732aa / d4ef0c2 |
| Design / schema docs | `docs/timing_model.md`, `docs/dataset_schema.md`, `docs/descriptors.md`, `docs/architecture.md`, README | M1–M5 |
| Freeze evidence | `FREEZE.json`, `SHA256SUMS`, `manifest.jsonl`, `audit.json`, `reproducibility.json` | 1de9447 |

Classes: **A** = mandatory requirement frozen before M6; **B** = design choice made in M1–M5.1; **C** = known limitation accepted before generation.

## 2. Requirements table

Status: PASS / PASS-KL (pass with known limitation) / PARTIAL / FAIL / N/A.

| ID | Cls | Original requirement | Source | Expected implementation | Audit method | Observed | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| R01 | A | Dataset frozen as fleetcan-sim-1.1, immutable, hashed | R-M6 §20–22 | versions, FREEZE, SHA-256, read-only | `sha256sum -c`, digests vs FREEZE.json, write test | 20,805/20,805 files OK; digests match; files read-only; last dataset file mtime 10:12:42 UTC < freeze 14:04 UTC | PASS | §3 |
| R02 | A | 122 configurations × 5 seeds = 610 sessions; no missing/duplicate/unexpected | R-M6 §3,13 | matrix_spec = protocol table; metadata = spec | independent reconstruction from protocol §4 | 122 = 122; 5 reps each; 0 duplicates; 610 unique seeds; metadata tags = spec | PASS | §4 |
| R03 | A | Fleet sizes 2/5/10 (architecture "at least 2, 5, 10, preferably more") | R-PLAN §7, R-M6 §4 | sessions at N = 2, 5, 10 | metadata | 150/210/250 sessions; 300/1,050/2,500 traces; max 10 | PASS-KL (Nmax = 10) | §5 |
| R04 | A | Three heterogeneous synthetic profiles (IDs, periods, phase, DLC, packing, scaling, message sets; not OEM) | R-PLAN §10, R-M5 §2 | profiles_abc.yaml + 3 DBCs | config/DBC inspection | genuinely different (table §6); labelled synthetic/not OEM | PASS | §6 |
| R05 | A | Seeded balanced_shuffle; N<3 seeded subset w/o replacement; reproducible | R-M5 §3, R-M51 §1 | config.build_fleet | independent re-derivation for all 610 sessions | 610/610 identical; N=2 pairs A+B 50, A+C 45, B+C 55 (all 6 orders); N=5 A2B2C1; N=10 A4B3C3 | PASS | §7 |
| R06 | A | SUMO is the mobility source and logical master clock; CAN derived from SUMO state via synthetic model | R-PLAN §7,8,23 | sumo_controller + can_generator | code + V09/V10/V17 + states | 10 ms step everywhere; integer-ns clock; decoded speed = SUMO speed (V17 PASS ×610) | PASS | §8 |
| R07 | A | Common time base; generation vs transmission vs capture time kept distinct | R-PLAN §23, R-M2 I | three time fields | schema + checks | sim_time_ns / tx_start/tx_end_ns / capture_time_ns + capture_offset_us; tx ≥ generation everywhere | PASS | §9 |
| R08 | A | Periodic ECU scheduling, independent phases, switch-on U[0, 1 s] @ 1 µs, seeded | R-M5 §5, R-M6 §4 | can_generator + build_fleet | re-derivation; statistics | 3,850 offsets reproduced exactly; range 65 µs–0.99989 s; all multiples of 1 µs | PASS | §10 |
| R09 | A | ±1 % ECU release jitter per (vehicle, message) stream, seeded, no accumulation, ordering valid | R-M51 §2, R-M6 §4 | `_jittered`, per-stream RNG | every release time of 17,975 streams re-derived from seed | 0 mismatches; max |jitter| = 1.000 % of period; nominal grid preserved | PASS | §11 |
| R09b | A | First frame not before vehicle activation | R-M51 §2 (ordering), audit §9 | first release clamped | first frame vs SUMO entry and vs switch-on | never before SUMO entry; **787/3,850 traces (20.4 %) emit the first frame ≤ 99.9 µs before the nominal switch-on instant** (negative jitter on zero-offset streams; clamp is to SUMO entry, not switch-on) | PARTIAL (MINOR) | §11 |
| R10 | A | Logical Classical CAN bus: 500 kbit/s, exact stuffing + CRC, lowest-ID arbitration, one frame at a time, queue 32, drop_newest, 100 ms deadline, queue/expiry drops separate, before vCAN | R-M2 A, R-M3 §2, R-M6 §4 | bus_model.py | config; independent bit count (183,000 frames sampled); overlap; B01–B07 | all settings as frozen; bit counts and durations equal; 0 overlaps; B01–B07 PASS ×610 | PASS-KL (logical model) | §12 |
| R11 | A | Four attack families with frozen parameters | R-M6 §6 | attacks params | per attack: params, counts, IDs, payloads, rates | flooding 0x000/1600 Hz/8 zero bytes; fuzzy both/1000 Hz uniform IDs; malfunction vehicle_speed = 250 km/h @100 Hz; replay own bus, 5 s window, +0.25 ms; 5 s (10 s partial) | PASS | §13–17 |
| R12 | A | Attack validity: frames exist, window, vehicle, profile handling, GT parameters, replay linkage | R-M2 D–J | attacks / attack_frames / replay_map | independent checks | 0 issues; 1,197,957 replay links verified; malfunction decoded = 250 km/h on A/B/C targets | PASS | §16–17 |
| R13 | A | S0 contains no attack | R-PLAN §20 | S0 sessions | all 15 S0 | 0 malicious frames, attacks, campaigns, memberships | PASS | §18 |
| R14 | A | S1: one attacked vehicle, rest benign; all families/fleet sizes | R-M3 §13 | S1 configs | all 60 | exactly vehicle_01 attacked; 5 sessions per family × fleet size | PASS | §19 |
| R15 | A | S2 independent hard negatives: same family, close in time, separate campaigns, coordinated = false | R-PLAN §19F, R-M3 §9, R-M5 §18 | k single-vehicle campaigns | all 60 | k = 2/3/5 campaigns, coordinated = false, distinct IDs, size 1, starts spread 0.5 s | PASS | §20 |
| R16 | A | S3 coordinated: coordinated = true, membership from configuration, size, pattern | R-M3 §3–8 | one coordinated campaign | all 420 S3 | members vehicle_01..size; delays exact (ns); attack IDs = members | PASS | §21 |
| R17 | A | Campaign sizes 2 (N=2), 2/5 (N=5), 2/5/10 (N=10); size ≤ N; remaining benign | R-M5 §17, R-M6 §8 | matrix | coverage matrix | all intended cells present (§23) | PASS | §23 |
| R18 | A | Temporal patterns 0 / 0.5 / 2 / 10 s / partial (5 s steps, 10 s attacks) + 0.1 s replay probe | R-M6 §7 | relative delays | member delays (ns) | exact for every member | PASS | §24 |
| R19 | A | Observable vs ground-truth timing separated; detectors need no campaign timing | R-PLAN §21, R-M5 §22 | separate GT files; observation-only extractor | allow-list + reconstruction from observation columns only | 31,584 descriptors rebuilt from (can_id, dlc, payload, tx_end_ns) only; max rel. error 1.1e-15 | PASS | §25 |
| R20 | A | Mixed scenarios (N ≥ 5): coordinated + independent, separately identified | R-M3 §9, R-M6 §8 | S3-mixed | all 40 | coordinated size 2/5 + 1/2 independent (coordinated = false, separate IDs, distinct vehicles) | PASS | §22 |
| R21 | A | Ground truth separate, joined by stable IDs | R-PLAN §12, R-M3 §14 | raw vs ground_truth vs analysis | schemas, joins | labels 1:1 with frames by frame_uid; descriptor_labels 1:1 by descriptor_id | PASS (note: raw keeps `scenario_id`/`session_id` as required by R-PLAN §11) | §25 |
| R22 | A | frame_uid traceability (generation → bus → capture → GT; replay) | R-M2 G, R-M3 §14 | frame_uid | uniqueness, joins | unique, non-null, 1:1 labels; replay ↔ source verified | PASS | §26 |
| R23 | A | Exact FLEET-GUARD 24-D descriptor, 100/50, trailing windows dropped, NaN→0, timestamp = window end, no GT/score | R-M4, R-M6 §5 | analysis/ | column order, counts, reconstruction | identical; window counts = ⌊(n−100)/50⌋+1 for every trace | PASS | §27–28 |
| R24 | A | Descriptor traceability (IDs, first/last frame, times) | R-M4 | descriptor ID columns | span check in reconstruction | first/last frame UID match for every reconstructed window | PASS | §28 |
| R25 | A | Descriptor labels from member frames, separate, evaluation-only | R-M4 | descriptor_labels | recomputation | 0 label mismatches (31,584 windows); separate file | PASS | §28 |
| R26 | A | Profile × attack: every attack valid on every profile; malfunction semantic per profile | R-M5 §4 | role mapping | profile × family counts; decode | all 12 cells present; malfunction targets A 0x120 VehicleSpeed, B 0x1A0 VehSpd, C 0x131 Speed | PASS | §29 |
| R27 | A | Independent deterministic RNG streams (`SeedSequence([seed, crc32(tag)])`) | R-M51 §9 | derive_seed etc. | code + re-derivation | formula exact; assignment / switch-on / jitter / attack seeds reproduced for all sessions | PASS | §30 |
| R28 | A | Fast mode logically identical to real time (incl. saturation) | R-M5 §14, R-M51 §6 | M5/M5.1 evidence | read-only re-comparison | M51 saturation (gen. 1.1.0, ±1 % jitter): all tables, arbitration order, descriptors identical; M5 3/5/10-vehicle replay (gen. 1.0.0): identical | PASS-KL | §31 |
| R29 | A | Saturation representable with full accounting | R-M2 D, R-M3 §2 | counts | M51 report | generated 62,961 = transmitted 55,961 + queue 5,283 + expired 1,717; captured 55,961 | PASS | §32 |
| R30 | A | Parquet + ZSTD; readable; CSV equivalence | R-M51 §7, R-M5 §12 | storage.py | codec of every column chunk | 8,465 files, 100 % ZSTD, all readable | PASS | §33 |
| R31 | A | Metadata/provenance (seeds, versions, config snapshot, git, SUMO/Python/deps) | R-PLAN §13 | metadata.json, provenance/ | V23/V24/V25 + provenance | present; V23–V27 PASS ×610 | PASS | §3 |
| R32 | B | Independent hard negatives start spacing (0.5 s) | M5 design | S2 starts | GT | spread exactly 0.5 s at every N | PASS | §20 |
| R33 | B | Balanced counts favour A then B when N mod 3 ≠ 0 | M5 design | cycle A,B,C,A,B,… then shuffle | metadata | N=5 always 2A2B1C; N=10 always 4A3B3C | PASS (informational) | §7 |
| R34 | C | Known limitations documented | R-PLAN §25, R-M5 §22 | README/docs | doc review | synthetic/not-OEM/vCAN/logical-bus documented; **README "Limitations (Milestone 2)" section is stale** (says one profile, no jitter) and no consolidated limitations section for 1.1 | PARTIAL (MINOR) | §35 |
| R35 | A | No detector feedback into generation | R-PLAN §25, R-M5 §22, R-M6 | chronology, code | git, mtimes, grep | generator code unchanged since 3e732aa; dataset finished 10:12 UTC; first M7 commit 14:13 UTC; no detector imports in src/analysis | PASS | §36–37 |
| R36 | — | Random controlled delay pattern (seeded uniform) | R-PLAN §19D | scheduler capability | tests/code | implemented & tested; not in frozen matrix (patterns frozen in R-M6 §7) | N/A to dataset | §24 |
| R37 | — | S4 weak-evidence scenario | R-PLAN §20 | deferred by R-M3 §13 | — | not part of frozen matrix by decision | N/A | — |
| R38 | B | Freeze verification commit | R-M6 §2 (commit = 3e732aa) | generation at the frozen commit | metadata git | sessions record d4ef0c2 (= 3e732aa + matrix configs + runner; `src/`, `analysis/` identical) | PASS (informational) | §37 |

No requirement is FAIL. PARTIAL items: R09b (MINOR), R34 (MINOR).

## 3–5. Integrity, matrix, fleet sizes

* Integrity: `sha256sum -c SHA256SUMS` → 20,805 OK; SHA256SUMS `bc523937…`, manifest `aebfd8e0…`, matrix `4e6dd99d…` equal FREEZE.json;
  dataset/schema/generator/descriptor versions fleetcan-sim-1.1 / 1.0 / 1.1.0 / 1.0 in all 610 metadata files; provenance/ present (12 entries).
* Matrix: expected (from the protocol table) = spec = metadata: N=2 30, N=5 42, N=10 50 configurations; 5 repetitions each.
* Fleet: N=2 150 sessions / 300 traces; N=5 210 / 1,050; N=10 250 / 2,500; total 3,850 traces; min 2, **max simultaneous fleet = 10 vehicles**.

## 6. Profiles (synthetic, not OEM)

| | A | B | C |
|---|---|---|---|
| DBC | generic_vehicle.dbc | generic_vehicle_b.dbc | generic_vehicle_c.dbc |
| IDs | 0x100,0x120,0x150,0x180,0x200 | 0x0C1,0x0D0,0x1A0,0x2B0 | 0x101,0x130,0x131,0x190,0x200 |
| Messages / rate | 5 / 350 fps | 4 / 270 fps | 5 / 350 fps |
| Periods ms | 10,20,20,20,10 | 20,10,10,50 | 10,20,20,20,10 |
| Phases ms | 0,1,3,5,7 | 2,4,6,8 | 0.5,2.5,4.5,6.5,8.5 |
| DLC | 8 | 8 | 6,8,6,4,5 |
| Speed role | VEHICLE_SPEED.VehicleSpeed 0.01 km/h | ESC_SPEED.VehSpd 0.05 km/h | VEHICLE_DYNAMICS.Speed 0.01 km/h |
| Packing | one message per function | pedal + brake share PEDAL_STATUS | throttle in ENGINE_DATA, extra WHEEL_SPEEDS |
| Counter | 4-bit Counter | 4-bit AliveCounter | 4-bit Counter (none in WHEEL_SPEEDS) |

Same SUMO state and physical model; different CAN realisations → not copies. Described as synthetic/not OEM in
profiles_abc.yaml, final_protocol §2, README. (profiles_abc.yaml header points to a non-existent `docs/profiles.md`; the
content is in final_protocol §2 — documentation nit.)

## 7–12. Assignment, SUMO, clock, scheduling, jitter, bus

* Assignment re-derived for 610/610 sessions. N=2 ordered pairs AB 24, BA 26, AC 21, CA 24, BC 29, CB 26.
* SUMO 1.28.0, step 10 ms, integer-ns clock (`step_index × step_ns`), states for every vehicle at every step (equal counts per vehicle),
  speed/accel/position/angle/lane/edge/route recorded; RPM/gear/pedal/brake/steering are synthetic functions of SUMO kinematics (can_generator.SyntheticVehicleModel).
  SUMO does not generate CAN.
* Time bases: generation `sim_time_ns` (SUMO, ground truth), modelled `tx_start_ns`/`tx_end_ns`, capture `capture_time_ns`
  (host wall clock) + `capture_offset_us`. Descriptor timestamp = window end on `tx_end_ns` (observable bus time). Campaign timing exists only in ground truth.
* Jitter: 17,975 streams, every release time reproduced from `derive_seed(derive_seed(seed,"ecu_jitter"),"ecu_jitter/<v>/<m>")`; no drift.
* R09b detail: the first release is `max(SUMO entry, nominal + jitter)`. For streams with offset < 1 % of the period
  (Profile A ENGINE_STATUS, offset 0) a negative draw places the first frame up to 0.1 ms before `entry + switch_on`. Validator V10 tolerates exactly this.
  Effect: ≤ 0.1 ms on one frame per trace; never before the vehicle exists; ordering/monotonicity unaffected. Reported, not fixed.
* Bus: per profile `bitrate 500000, stuffing exact, queue_limit 32, drop_newest, priority, deadline 100 ms`; arbitration precedes vCAN
  (`BusTx` → `CanTransport`); vCAN documented as transport/capture only (timing_model.md, README).

## 13–17. Attacks

| Family | Frozen | Observed (all sessions) |
|---|---|---|
| Flooding | 0x000, 1600 fps, 5 s | ID 0x000, DLC 8, 00×8, generated = 1600 × duration (8,000; 16,000 partial) |
| Fuzzy | random ID + payload, 1000 fps, 5 s | mode `both`, IDs uniform over 0x000–0x7FF (unique-ID count ≥ 90 % of the uniform expectation), random DLC |
| Malfunction | vehicle_speed = 250 km/h, 100 fps, 5 s | inject on each profile's own speed message/signal; decoded 250 km/h (within DBC resolution); next legitimate counter |
| Replay | own bus, 5 s source window, 5 s attack, +0.25 ms | source = benign frames of the same vehicle with tx_end ∈ [w0, w1); replay generation = start + (tx_end − w0) + 0.25 ms (exact); window 5 s (10 s for partial) |

Attacked vehicles: flooding 630, fuzzy 630, malfunction 630, replay 660; malicious frames 5.72 M / 3.575 M / 0.3575 M / 1.198 M.

## 18–24. Scenarios, campaign sizes, temporal patterns

* S0 15 sessions: 0 malicious frames. S1 60: vehicle_01 only (attacked profiles A 14, B 23, C 23).
* S2 60: k = 2/3/5 single-vehicle campaigns, `coordinated=false`, starts 10.0–10.5 s.
* S3 420: one coordinated campaign each; S3-mixed 40: (N=5) size 2 + 1 independent, (N=10) size 5 + 2 independent; S3-probe 15.
* Coverage S3 (sessions): N=2 size 2: 25 per family; N=5 size 2: 10, size 5: 25 per family; N=10 size 2: 10, size 5: 10, size 10: 25 per family.
* Delays (s): simultaneous 0; small 0, 0.5, …; medium 0, 2, …; large 0, 10, …, 90; partial 0, 5, …, 45 (10 s attacks); probe 0, 0.1.

## 25–30. Separation, identity, descriptors, RNG

* Raw `can_frames` columns: frame_uid, session_id, scenario_id, timestamps (generation/transmission/capture), vehicle_id, channel, can_id, dlc, payload.
  `scenario_id`/`session_id` are required raw fields (R-PLAN §11); they and generation-time fields are on the extractor's prohibited list.
  Labels, attack/campaign IDs, coordination and delays are only in ground_truth/.
* Reconstruction: 28 sessions (all scenario types, all fleet sizes), 31,584 windows rebuilt from observation columns only:
  max relative feature error 1.1e-15, first/last frame UID 100 % equal, 0 label mismatches.
* RNG: `derive_seed(seed, tag) = int(SeedSequence([seed, crc32(tag)]).generate_state(1)[0])`; campaigns `SeedSequence([seed, crc32("campaign:"+id)])`;
  attacks `SeedSequence([seed, crc32(attack_id)])` — attack seeds re-derived for every attack. M6 regeneration spot checks (3 sessions) identical.

## 31–33. Fast mode, saturation, storage

* Fast vs real time (re-compared read-only today): M51 over-saturation flood (generator 1.1.0, ±1 % jitter, 3 vehicles) — all tables, per-vehicle arbitration order and
  descriptors identical; M5 replay campaigns 3/5/10 vehicles (generator 1.0.0, no jitter) identical. Known limitation: the multi-vehicle real-time comparison
  predates jitter; the jittered real-time comparison is the 3-vehicle saturation case.
* Saturation accounting identical in both modes (62,961 / 55,961 / 5,283 / 1,717 / 55,961).
* Storage: Parquet/ZSTD 100 %; 2.48 GB for the 610 sessions; 49,340,597 frames, 981,088 descriptors, 3,850 traces, 120,778 vehicle-seconds.

## 34. Descriptive statistics (no detection)

Sessions: S0 15, S1 60, S2 60, S3 420, S3-mixed 40, S3-probe 15; flooding 145, fuzzy 145, malfunction 145, replay 160.
Frames benign 38,490,140 / malicious 10,850,457. Windows benign_only 665,782 / mixed 315,306 / malicious_only 0.
Profile traces: A 1,515, B 1,275, C 1,060 (N=2: A 95, B 105, C 100; N=5: 420/420/210; N=10: 1,000/750/750; C minority by R33). Session durations 20–110 s.

## 35. Known limitations (documented vs not)

Documented: synthetic CAN signals and profiles (not OEM), SUMO-based dynamics, no real ECUs/hardware, vCAN not a physical bus, logical arbitration model
(no error frames, bit-level races, physical layer), single-vehicle independent ±1 % jitter, Nmax = 10, finite sessions (20–110 s), controlled attack implementations.
Not consolidated in one place / stale: README limitations section dates from M2; "no long-term oscillator drift" and "no correlated ECU scheduling across ECUs"
are implied by the jitter model but not stated as limitations. (R34, MINOR, documentation only.)

## 36–37. Detector independence and tuning history

* `src/` and `analysis/` unchanged since 3e732aa (generation) — `git diff 3e732aa HEAD -- src analysis dbc config/profiles config/final/fleets sumo` is empty.
* Dataset files last written 10:12:42 UTC; M7 started 14:13 UTC (3f0c83d). No import of detection/, sklearn, results/ in the generator.
* Pre-generation parameter choices and their stated criteria: switch-on range (window-grid phase sensitivity, M5), ±1 % jitter (smallest level avoiding
  near-zero benign variance; criterion explicitly "not attack separability", M5.1), delay set (measured descriptor timing resolution, M4/M5/M5.1),
  100 ms deadline (stale-frame realism, M3), attack parameters (frozen in M5 protocol, re-checked for semantic correctness only in M5.1).
  The M4/M5 normalisation audit reported descriptor variance, not detector scores; no Isolation Forest / PR-AUC / F1 existed before M7.
  No evidence of detector-performance tuning found.

## 38–41. Scalability readiness (assessment only — nothing executed)

| Component | Status | 20 | 50 | 100 | 250 | 500 |
|---|---|---|---|---|---|---|
| Vehicle count / IDs | `fleet.count`, `vehicle_{n:02d}` (3 digits ≥ 100; lexicographic order changes, IDs stay unique) | ok | ok | cosmetic | cosmetic | cosmetic |
| Profile assignment | any N | ok | ok | ok | ok | ok |
| SUMO scenario | **hand-written routes only for 2/5/10 on a 4×4 grid, all depart t = 0 on distinct edges** | **blocker** | **blocker** | **blocker** | **blocker** | **blocker** |
| vCAN interfaces | `setup_vcan.sh N` (sudo, non-persistent) **caps N ≤ 64** | ok | ok | **blocker** | **blocker** | **blocker** |
| Sockets / threads | ~1 listener thread + socket per vehicle and one sender socket per (vehicle, node); `ulimit -n` = 1024 | ok | ok | risk | **blocker** | **blocker** |
| CAN generators / bus models | one object per vehicle, created dynamically | ok | ok | ok | ok | ok |
| Campaign scheduler / GT / recorder / descriptors / validator | no hard-coded vehicle limit found | ok | ok | ok | ok | ok |
| CPU (single process) | measured 0.062–0.116 s wall per vehicle-second (N=10: 0.62 s per simulated s) | ~1.2× RT | ~3× | ~6× | ~15× | ~30× slower than real time (fast mode only) |
| RAM (all frames of a session held until the end) | ~350 fps × N × duration objects | ok | ok | check | **risk** (≈3 M frames) | **blocker** on 7.9 GB VM (≈6–7 M frames) |
| Storage | ≈ 20 KB per vehicle-second Parquet | small | small | ≈ 80 MB/40 s | ≈ 0.2 GB | ≈ 0.4 GB per 40 s session |

A scalability milestone must at least provide a route/network generator (a new mobility design decision), lift the vCAN cap,
raise fd limits or share sockets, and test memory/streaming behaviour — without changing CAN, attack, timing or descriptor semantics.

## 42. Commands (read-only)

```
cd ~/fleet-can-dataset/data && sha256sum -c --quiet final/fleetcan-sim-1.1/SHA256SUMS
cd ~/fleet-can-dataset && .venv/bin/python scripts/final_requirements_audit.py --workers 6
.venv/bin/python scripts/m51_saturation_report.py
for n in 3 5 10; do .venv/bin/python scripts/compare_sessions.py M5_BENCH_REPLAY_${n}V_PQ_RT M5_BENCH_REPLAY_${n}V_PQ_FAST; done
git diff --stat 3e732aa HEAD -- src analysis dbc config/profiles config/final/fleets sumo
.venv/bin/python -m pytest -q tests
```
