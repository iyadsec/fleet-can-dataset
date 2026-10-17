# Milestone 9.1 — final matrix freeze and pre-generation validation

**Verdict: MATRIX FROZEN — WAITING FOR GENERATION AUTHORIZATION.** The disk-capacity gate is a separate check and
is currently **FAIL**, which is expected until the storage extension is done. No research session was generated.
fleetcan-sim-1.1 is byte-identical before and after M9.1.

## Identity

| Item | Value |
|---|---|
| matrix_id | `fleetcan-sim-scale-1.0-matrix-v1` |
| component | `fleetcan-sim-scale-1.0` (no `fleetcan-sim-2.0` umbrella) |
| file | `configs/fleetcan_sim_scale_1_0.yaml` (1,149,884 bytes, read-only on the VM) |
| SHA-256 | `9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735` |
| matrix commit | `58bd4454c1909c971cec3187d2f47f8b43738a5f` |
| generator | 1.3.0, source commit `559179f` (last change to src/, analysis/, config/profiles, sumo/) |
| schema / descriptor | 1.0 / 1.0 |
| configurations / repetitions / sessions | 167 / 5 / 835 |
| expected_sessions.csv SHA-256 | `807bf10a26fbff7f27bc05b3069c7553c97b97bbb6bd123789d1bc9a46cb7b52` |
| status | `matrix_status: frozen`, `approved_for_generation: false` |
| manifest | `results/m9_1_freeze/matrix_freeze_manifest.json` (frozen 2026-10-17T07:52:24Z) |

**Change policy.** The matrix is immutable. `scripts/m91_freeze.py` rebuilds it byte-for-byte (validator check
C23) and refuses to overwrite it with different content. Any scientific change requires a new matrix version.
Each configuration keeps its `config_index`, and with it its seeds, so a future version can only append
configurations.

## Content

* **Main grid:** design B, exactly as approved in M9 — 162 configurations, 810 sessions. Every field and every
  campaign member is identical to the M9 proposal (check C24).
* **G0 route-source bridge:** 5 configurations, 25 sessions. Purpose `route_source_bridge`, phase `G0`, IDs
  `FCS10_G0_N010_*`:

| config_id | scenario | K | pattern | family |
|---|---|---|---|---|
| FCS10_G0_N010_S0_none_K000_none | S0 | 0 | — | — |
| FCS10_G0_N010_S3_replay_K005_simultaneous | S3 | 5 | simultaneous | replay |
| FCS10_G0_N010_S3_flooding_K005_simultaneous | S3 | 5 | simultaneous | flooding |
| FCS10_G0_N010_S3_fuzzy_K005_small | S3 | 5 | 0.5 s | fuzzy |
| FCS10_G0_N010_S3_malfunction_K005_small | S3 | 5 | 0.5 s | malfunction |

  The G0 attack families follow the M9 rotation rule:
  - 0.5 s stagger uses `latin(rot(20, 5))` = [fuzzy, malfunction], the same pair as the matching scaled cell
    (N = 20, K = 5, 0.5 s).
  - Simultaneous uses the complementary pair `latin(3)` = [replay, flooding].
  - Together the five G0 configurations cover all four families.
* **G1:** absent. There are no N = 10 configurations with 2 s, 10 s or partial-overlap timing (check C05).

| Phase | configurations | sessions | content |
|---|---|---|---|
| 1 core | 84 | 420 | S0, S1, S2 near-simultaneous, S3 simultaneous grid, replay probe, mixed |
| 2 timing | 78 | 390 | S3 0.5 s grid, S2 staggered, S2 separated, K = 5 temporal ladder, bounded spread |
| G0 bridge | 5 | 25 | route-source bridge (N = 10) |

Configurations per scenario:

| | S0 | S1 | S2 | S3 | S3-probe | mixed |
|---|---|---|---|---|---|---|
| Configurations | 4 (incl. G0 1) | 12 | 36 | 106 (incl. G0 4) | 3 | 6 |

The S2 count breaks down as 18 near-simultaneous, 12 staggered and 6 separated.

Each session in the YAML carries:
- seed;
- profile composition (N_A/N_B/N_C, K_A/K_B/K_C);
- explicit `isolated_attacks` and `campaigns`, using the baseline naming convention (`A_S1_<F>`,
  `C_S2_<F>_<j>`, `C_S3_<F>`, `C_PROBE_REPLAY`, `C_MIX_<F>`, `C_MIX_<F>_IND<j>`).

The M10 runner therefore only translates the matrix into experiment files; it never re-derives targets.

## G0: what differs from the baseline N = 10 sessions

| Aspect | fleetcan-sim-1.1 N = 10 | G0 |
|---|---|---|
| Route source | hand-written `sumo/routes/fleet_10.rou.xml`: fixed loop and start edge per vehicle ID (P, P, I, P, R, I, P, R, I, I) | `grid_loops_v1`: seeded slot draw (`derive_seed(seed, "route_generation")`), 30 m slots on the same three loops |
| Network, vType, departure | grid4x4, `car_a`, depart t = 0, speed 0 | identical |
| Fleet configuration | `config/final/fleets/fleet_10.yaml` | identical file (vehicle_01 … vehicle_10, A/B/C balanced_shuffle, switch-on U[0, 1] s @ 1 µs, ±1 % jitter) |
| Campaign members / IDs / timing / parameters | vehicle_01 … 05, `C_S3_<F>`, start 10 s, frozen parameters | identical (test `test_g0_is_baseline_except_route_source`) |
| Generator | 1.1.0 | 1.3.0 (M8.1: output-identical to 1.1.0 on the frozen route source at N = 10) |
| Seeds | 610xxx | 921621–921665 (as for any new session) |

G0 provides the data to *measure* the route-source effect later. It does not prove equivalence, and M9.1 claims
none.

G0 also differs from the scaled component (N ≥ 20) in two assignment conventions:
- **Member rule:** first K vehicles (`baseline_first_k`) instead of seeded profile-balanced selection.
- **ID format:** 2-digit instead of 3-digit vehicle IDs.

## Seeds and RNG namespaces

**Master seed:** `920000 + 10·config_index + repetition`, giving 920001–921665. All 835 are unique. None collides
with the 669 existing seeds, which span 42–611215 (baseline 610001–611215; M8/M8.1/M9.1 engineering 80010–83110).
`expected_sessions.csv` reproduces every seed from the matrix alone.

| Namespace | Derivation |
|---|---|
| SUMO | `--seed seed` |
| route generation | `derive_seed(seed, "route_generation")` |
| profile assignment | `derive_seed(seed, "profile_assignment")` |
| switch-on | `derive_seed(seed, "switch_on")` |
| ECU jitter | `derive_seed(seed, "ecu_jitter")` → `ecu_jitter/<vehicle>/<message>` |
| attack | `SeedSequence([seed, crc32(attack_id)])` |
| campaign | `SeedSequence([seed, crc32("campaign:" + id)])` (only for uniform_random, unused here) |
| campaign members | `derive_seed(seed, "campaign_members/C_MAIN")` (builder only) |
| independent targets | `derive_seed(seed, "independent_targets")` (builder only) |
| isolated target | `derive_seed(seed, "isolated_target")` (builder only) |

The three selection streams are consumed offline. Their results are frozen as explicit targets, so the
generator never draws from them, and changing one condition cannot perturb another configuration's streams.
Selection inputs are the seed and the frozen fleet only — no detector, anomaly score, descriptor similarity or
M7 result.

## Audits (scripts/validate_m91_matrix.py, 26 checks, all PASS)

| Check | Content |
|---|---|
| C01–C03 | 167 configurations; 835 sessions; R = 5; session ID = `<config_id>_R<rep>` |
| C04–C05 | G0 exactly as approved (shape, families, purpose, fleet, member rule); G1 absent; N ∈ {10, 20, 50, 100} |
| C06 | 167 unique, deterministic, readable config IDs; no collision with baseline configuration IDs, engineering experiment IDs or existing sessions |
| C07 | 835 unique session IDs; none exists among 1,114 existing session / engineering IDs; CSV consistent |
| C08 | seed formula, uniqueness, no collisions |
| C09–C11 | K ≤ N; members recomputed identically from seed; disjoint; within fleet. Profile balance: all 555 scaled coordinated sessions have K_A/K_B/K_C spread exactly 1 (no K is a multiple of 3); G0 composition reported only (baseline rule) |
| C11 (S1) | every new N has targets of all three profiles across its 20 S1 sessions (N = 20: A 7 / B 9 / C 4; N = 50: 5 / 3 / 12; N = 100: 7 / 5 / 8) |
| C12 | frozen scheduler (`build_schedule`), scenario definition and a full **generator preflight**: each of the 835 sessions is loaded through `load_experiment` and `build_attacks`, asserting frozen fleet, jitter, switch-on, bus settings (500 kbit/s, exact stuffing, queue 32, drop_newest, 100 ms deadline) and step length 0.01 s |
| C13 | attack parameters equal the fleetcan-sim-1.1 matrix files (S3 and S1 per family; replay partial window 10 s) |
| C14 | delays reproduce each pattern exactly in the scheduler's integer ns; S2 and mixed starts |
| C15 | bounded spread: t_i = t_0 + W·i/(K−1) to ±1 ns, t_{K−1} − t_0 = W, and not equal to any sequential Δ·i rule |
| C16 | partial overlap: duration 10 s, spacing 5 s, consecutive overlap exactly 5 s, members two apart overlap 0. Convention is half-open [start, end); replay members end +0.25 ms after the nominal end |
| C17 | duration: warm-up ≥ 10 s before the first attack, last member end + 5 s tail ≤ duration, no truncation; replay sources inside the session, before each member's start, own bus. The tail of ≥ 5 s holds ≥ 1,600 frames per vehicle, so descriptor windows of 100 frames (stride 50) after the attack complete |
| C18 | S2: coordinated = false, one target per campaign, distinct campaign IDs, same family |
| C19 | S3: exactly one coordinated campaign holding all K members; mixed independents are coordinated = false |
| C20–C22 | status frozen; approved_for_generation false; no runner references the matrix (only the builder and validator) |
| C23 | deterministic rebuild byte-identical; manifest hashes match |
| C24 | 162 main configurations and all members identical to the approved M9 proposal |
| C25 | no change to src/, analysis/, config/profiles, sumo/, fleet configs or attacks.yaml since `559179f`; versions 1.3.0 / 1.0 / 1.0. This covers profiles, rates, DLCs, IDs, payload logic, switch-on, jitter, the 10 ms clock, the bus model, vCAN isolation and descriptors |
| C26 | provenance tags required by the matrix |

**Partial-overlap interval convention.** Member i is active on [10 + 5i, 20 + 5i) s. Members i and i+1 overlap
on [15 + 5i, 20 + 5i), i.e. 5 s. Members i and i+2 only touch at 20 + 5i, which is zero overlap. For replay, all
ends are +0.25 ms.

`tests/test_m91.py` has 23 tests:
- 17 negative tests: dropped G0 config, added G1 config, approved = true, status ≠ frozen, S2 coordinated,
  S3 not coordinated, wrong bounded delay, bounded written as sequential, partial with 5 s attacks, flooding
  1500 fps, truncated duration, duplicate seed, baseline seed, baseline session ID, K > N, foreign member,
  invalid attack;
- plus rebuild determinism, refusal to overwrite, the G0 baseline equality, counts, and the session-state
  classification.

Full suite: 287 passed.

## Resumability and provenance

The orchestrator already provides the safety mechanisms:
- staging in `data/.incomplete/<sid>`;
- validation inside the stage;
- publication by `os.replace`, metadata last;
- removal of the stale stage of an interrupted run on re-run;
- refusal to overwrite an existing session.

`scripts/session_state.py` defines the completion predicate the M10 runner must use. A session is complete when
all published directories exist, the metadata parses, generator validation is PASS, analysis
`descriptor_validation.json` is PASS, and the provenance tags match the matrix SHA. The other states are
`absent`, `staged_incomplete`, `partial_published`, `generated_validation_failed`, `analysis_pending`,
`analysis_failed` and `provenance_mismatch`. `clear_partial` deletes only states without metadata, and never
FC11 sessions.

**Engineering test** (`scripts/m91_resume_test.py`, ID `M91_RESUME_N010`, seed 83110, G0-shaped: baseline
N = 10 fleet, generated routes, fuzzy K = 5, 0.5 s stagger): **PASS**.

1. SIGKILL mid-run → `staged_incomplete`, nothing published, no orphan processes.
2. Re-run → validation PASS, stage removed.
3. Analysis → `complete`; tags (component, matrix_id, matrix_sha256, …) recorded in metadata with versions and Git
   commit.
4. Third run refused, every byte unchanged.
5. Interrupted analysis → `analysis_pending` → re-run gives identical descriptors.
6. Simulated crash during publication → `partial_published`; the orchestrator refuses it; `clear_partial`;
   regeneration is logically identical to the first run (94,336 frames, 1,873 descriptors, max feature difference
   0). File-level hashes differ only through host capture timestamps, which are non-authoritative as in M8.1.

**Requirements for the M10 runner** (to be written in M10, not now):
- Use the `session_state` predicate. The M6 runner treated metadata presence as complete, which would skip a
  session whose analysis failed.
- Never auto-overwrite `generated_validation_failed` or `analysis_failed` sessions. The orchestrator publishes
  such sessions with their failing report, so they need a human decision.
- Write the tags listed in `provenance_tags_required` — component, matrix_id, matrix_sha256, config/session IDs,
  repetition, N, K, scenario, attack, timing, route source, members, composition — through `experiment.tags`.
  These go to metadata only, never into descriptor tables.

## Cost (`results/m9_1_freeze/cost_estimate.json`, measured coefficients)

| | sessions | simulated h | vehicle-h | frames | descriptors | raw | GT/descr./meta | total | generation | analysis | wall |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Total** | 835 | 6.04 | 346.8 | 448.1 M | 8.92 M | 20.2 GB | 3.4 GB | **23.6 GB** | 16.4 h | 2.3 h | **18.7 h** |
| Phase 1 | 420 | 2.46 | 144.1 | 188.2 M | 3.75 M | 8.5 | 1.5 | 9.9 GB | 6.9 h | 1.0 h | 7.9 h |
| Phase 2 | 390 | 3.40 | 201.0 | 257.5 M | 5.12 M | 11.6 | 1.9 | 13.5 GB | 9.4 h | 1.3 h | 10.7 h |
| G0 | 25 | 0.17 | 1.7 | 2.4 M | 0.05 M | 0.11 | 0.02 | 0.13 GB | 0.10 h | 0.02 h | 0.12 h |
| N = 10 / 20 / 50 / 100 | 25 / 245 / 270 / 295 | | | 2.4 / 48.6 / 127.4 / 269.7 M | | | | 0.13 / 2.6 / 6.7 / 14.2 GB | | | 0.12 / 2.2 / 5.3 / 11.1 h |

By scenario: S0 0.60, S1 1.15, S2 5.03, S3 15.75, mixed 0.77, probe 0.29 GB.

Interpolation covers the measured range: N 10–100, 20–180 s, K ≤ 100 for replay. **Extrapolated:** the attack load
of flooding/fuzzy campaigns at K = 50/100. Calibration error was +1.9 % or better for frames, +6.7 % for storage
and +10 % for time. G0 was checked against the measured N = 10 session: frames −0.5 %, time +9 %.

## Disk-capacity gate (`results/m9_1_freeze/disk_gate.json`)

**Current state:**

| Item | Value |
|---|---|
| Filesystem size | 53.16 GB |
| Used | 30.85 GB |
| Free (user) | **19.80 GB** |
| Root-reserved | 2.51 GB (4.7 %) |
| Volume group unallocated | ≈ 11.25 GB (vda3 65.44 GB − LV 54.19 GB, from `lsblk -b`; the exact value needs `sudo vgs`) |

**Minimum free space before M10:**

| Component | Size |
|---|---|
| predicted dataset 23.58 GB × 1.10 (uncertainty) | 25.93 GB |
| generated SUMO scenarios | 0.13 GB |
| temporary (2 × largest staging peak 0.18 GB, at least) | 1.0 GB |
| operational reserve | 3.0 GB |
| **Total** | **30.06 GB** |

**DISK CAPACITY GATE: FAIL** — a shortfall of 10.26 GB.

Extending the logical volume over the whole unallocated VG space would give about 30.5 GB free. That meets the
minimum, but with only about 0.45 GB to spare. **Recommendation:** extend the LV *and* grow the virtual disk so that
at least about 35 GB is free before M10. That is your action; M9.1 changed no system setting and did not reduce the
matrix.

## Baseline integrity

| | Before (07:28:58Z) | After |
|---|---|---|
| sha256sum -c | 20,805 / 20,805 OK | 20,805 / 20,805 OK |
| SHA256SUMS | bc523937… | bc523937… |
| FREEZE.json | df58000a… | df58000a… |
| manifest | aebfd8e0… | aebfd8e0… |
| matrix spec | 4e6dd99d… | 4e6dd99d… |
| sessions | 610 | 610 |
| writable files | 0 | 0 |

Full records: `results/m9_1_freeze/baseline_integrity_{before,after}.txt`.

## Limitations

* The disk gate fails until storage is extended.
* S1 target profiles are seeded and uneven per N (for example C 4 of 20 at N = 20), although all three profiles
  are present.
* G0 campaign composition follows the baseline first-K rule and is not balanced.
* Host capture timestamps differ between regenerations; they are non-authoritative.
* Flooding/fuzzy cost at K = 50/100 is extrapolated.
* The M10 runner does not exist yet. Its required behaviour is specified above and tested at the primitive level.
* The engineering test session `M91_RESUME_N010` remains in `data/` (git-ignored, 5.4 MB), like the earlier
  M8/M8.1 engineering sessions.
