# Milestone 10 — controlled expanded dataset generation (fleetcan-sim-scale-1.0)

**Verdict: M10 PASS — EXPANDED DATASET GENERATED AND FROZEN.**

All 835 sessions of the frozen matrix were generated, validated, frozen and read-only.
fleetcan-sim-1.1 is byte-identical. No detector or correlation method was run.

## Authorisation and scope

* **Matrix.** `configs/fleetcan_sim_scale_1_0.yaml`, `fleetcan-sim-scale-1.0-matrix-v1`, SHA-256
  `9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735`. It has 167 configurations × 5 = 835
  sessions and stayed byte-identical; `approved_for_generation: false` was not edited.
* **Authorisation.** `scripts/run_m10_generation.py` refuses to run unless
  `--authorize-matrix-sha` equals both this SHA and the SHA of the actual file.
* **Scope change during M10 (your decision).** The component is raw CAN data plus separate ground truth only.
  * **Why:** descriptor extraction belongs to the FLEET-GUARD pipeline.
  * **Brief items dropped:** the descriptor items of the M10 brief (§8 steps 3–4, §11 descriptor checks, §21,
    descriptor statistics in §29) were withdrawn.
  * **Runner change:** the runner was switched to raw-only (commit `7b65963`).
  * **Earlier sessions:** the descriptor folders of the 116 sessions generated before the change were deleted.
    Their raw and ground-truth data were not touched.
  * **Baseline:** fleetcan-sim-1.1 keeps its descriptors, because it is immutable.
* **Per-session contents:**

| Directory | Content |
|---|---|
| `data/raw/<sid>/` | CAN frames, vehicle states, unmatched captures (observable data) |
| `data/ground_truth/<sid>/` | frame labels, attacks, attack frames, campaigns, membership, overlap, dropped frames, replay map |
| `data/metadata/<sid>/` | metadata incl. provenance tags, bus statistics, config snapshot |
| `data/validation/<sid>/` | generator validation report |
| `data/generated_sumo/<sid>/`, `data/m10_configs/<sid>/` | generated routes and the session configuration derived from the matrix |
| `data/final/fleetcan-sim-scale-1.0/` | manifest, provenance, statistics, validation summary, SHA256SUMS, FREEZE.json |

## Pre-generation gate

* **Baseline:** 20,805/20,805 files OK; SHA256SUMS `bc523937…`, FREEZE `df58000a…` and manifest `aebfd8e0…` all
  unchanged; 610 sessions; 0 writable files.
* **Matrix:** SHA verified; 167 configurations / 835 sessions; status frozen; no FCS10 sessions existed.
* **Tests:** 287/287 passed with vCAN up. The first run reported 8 skips because the vCAN interfaces did not
  exist yet when it started.
* **vCAN:** `sudo scripts/setup_vcan.sh 100`, run by you, gave exactly vcan0…vcan99, all UP.

## Runner and resumability

For each session, in deterministic order (phase 1, phase 2, G0; then config_index and repetition), the runner:

1. **Classifies** the final location with `session_state.py`, using raw-only completion plus provenance and seed:
   * COMPLETE → skip;
   * INCOMPLETE → clean and regenerate;
   * INVALID → STOP, never overwritten.
2. **Applies the gates:** at least 15 GB free disk, and a peak RSS limit of 3 GB.
3. **Writes the configs** from the matrix only.
4. **Generates** into the private staging root `data/.m10_stage`, where the orchestrator also stages and validates.
5. **Verifies:**
   * generator validation PASS;
   * all provenance tags, seed, versions and a clean git commit;
   * fleet and A/B/C assignment, and K/N composition;
   * campaigns and membership against the matrix in integer ns, coordinated flags, attack set and isolated
     attacks;
   * replay linkage;
   * unique frame UIDs and the vehicle↔channel mapping;
   * transport and capture accounting, with no flow-control timeouts;
   * no derived outputs.
6. **Publishes** with `os.replace`, metadata last, then re-classifies the session (it must be COMPLETE).
7. **Records** the result in the ledger and execution log, and runs a checkpoint with a baseline check at the end
   of each phase.

One interrupted session (stopped for the scope change) was found in staging. It was cleaned and regenerated
from scratch, which is the tested M9.1 semantics. 836 starts led to 835 completions.

## Pilot (9 frozen research sessions)

The pilot sessions were N20 S0, N50 S1 replay, N100 S2 flooding, N100 K100 replay, N100 K100 flooding,
N50 K5 partial-overlap replay, N100 K50 10 s bounded spread, N100 mixed, and G0 0.5 s fuzzy.

* **Result:** 9/9 PASS on every check.
* **Replay links:** 1,750, 161,799 and 15,679 in the three replay sessions.
* **Peak RSS:** 1,515 MB.
* **Baseline:** PASS.

## Phase results (runner checkpoints, each with full fleetcan-sim-1.1 integrity)

| Checkpoint | Sessions | Frames | Storage | Generation time | Peak RSS | Free disk | Baseline |
|---|---|---|---|---|---|---|---|
| Phase 1 core | 420 / 420 | 185,944,809 | 9.11 GB¹ | — | 1,666 MB | 58.63 GB | PASS |
| Phase 2 timing | 390 / 390 (cumulative 811) | 256,028,014 | 12.61 GB | 14.69 h | 1,774 MB | 46.04 GB | PASS |
| G0 bridge | 25 / 25 (cumulative 835) | 2,360,399 | 0.12 GB | 0.13 h | 581 MB | 45.92 GB | PASS |
| **All** | **835 / 835** | **444,333,222** | **21.84 GB** | **25.84 h** | **1,774 MB** | 45.92 GB | PASS |

¹ The ledger's Phase 1 storage figure includes the 116 descriptor folders that were deleted later.

There were no failures, no INVALID sessions, no retries and no validator warnings.

## Validation (`scripts/m10_finalize.py validate`, independent of the runner, on the published data)

* **Counts:** 835/835 sessions COMPLETE and re-verified against the matrix: 167 configurations × exactly 5
  repetitions.
* **Nothing missing, duplicated or extra:** no missing sessions, no duplicates, no unexpected `FCS10_*` session,
  and no `data/analysis/FCS10_*`.
* **Generator:** validation PASS in all 835 sessions, covering V01–V27 and the bus, attack (A01–A07) and campaign
  (C00–C08) checks, with 0 warnings.
* **Ground truth:**
  * S3: coordinated = true, with membership equal to the matrix targets.
  * S2: one coordinated = false campaign per attack, with distinct IDs.
  * Mixed: the coordinated and independent components are kept distinct.
  * Isolated S1 targets match the matrix.
  * Ground truth is always taken from the configuration, never inferred from timing.
* **Timing:** member start and end match the frozen scheduler exactly in integer ns for every pattern
  (simultaneous, 0.5/2/10 s, partial, bounded 0.5/2/10 s, 0.1 s probe). Bounded spread was additionally checked
  against t_i = t₀ + W·i/(K−1) to 1 ns.
* **Replay:** 210 replay sessions with 5,030,342 links, at least 1,350 per session. In every link:
  * source = target = member vehicle, on the own bus;
  * the original frame exists;
  * the original lies in the member's window;
  * order is preserved;
  * generator check A07 confirms the exact time shift and identical ID and payload.
* **Profiles:** the A/B/C assignment equals the seeded frozen assignment, and K_A/K_B/K_C equal the matrix
  composition.
* **Provenance:**
  * Every session records component, matrix ID and SHA, config, session, repetition, seed, phase, purpose, N, K,
    scenario, attack, timing, route source, member rule, members and composition.
  * It also records generator 1.3.0, schema 1.0 and the Git commit with `dirty = false`.
  * 116 sessions record `f212877` and 719 record `7b65963`. Between the two, only the runner changed; `src/` is
    identical.
* **Separation of observable data and ground truth:** all labels, campaign and attack identities, configured
  times and coordinated flags are in `ground_truth/`. The raw frame table (schema 1.0, identical to
  fleetcan-sim-1.1) also carries the identifiers `session_id` and `scenario_id`. `scenario_id` is a scenario-level
  label, so a detector-side pipeline must drop it, as the baseline's descriptor layer did.

## Dataset statistics (descriptive only)

| | Sessions | Frames | Total GB | Raw GB | GT GB | Generation h | Simulated h |
|---|---|---|---|---|---|---|---|
| N = 10 (G0) | 25 | 2,360,399 | 0.12 | 0.10 | 0.01 | 0.13 | 0.17 |
| N = 20 | 245 | 48,075,245 | 2.42 | 2.10 | 0.21 | 2.65 | 1.79 |
| N = 50 | 270 | 126,209,411 | 6.17 | 5.50 | 0.47 | 7.19 | 1.96 |
| N = 100 | 295 | 267,688,167 | 13.02 | 11.76 | 0.93 | 15.87 | 2.11 |
| S0 | 20 | 11,499,140 | 0.56 | 0.52 | 0.02 | 0.68 | 0.22 |
| S1 | 60 | 21,665,668 | 1.08 | 0.99 | 0.05 | 1.33 | 0.33 |
| S2 | 180 | 95,370,893 | 4.69 | 4.28 | 0.28 | 5.53 | 1.40 |
| S3 | 530 | 295,744,898 | 14.41 | 12.78 | 1.20 | 17.12 | 3.80 |
| S3-mixed | 30 | 14,621,957 | 0.71 | 0.64 | 0.05 | 0.86 | 0.20 |
| S3-probe | 15 | 5,430,666 | 0.27 | 0.25 | 0.02 | 0.31 | 0.08 |
| **Total** | **835** | **444,333,222** | **21.73** | **19.46** | **1.62** | **25.84** | **6.04** |

* **By attack** (sessions / frames):

| Attack | Sessions | Frames |
|---|---|---|
| flooding | 205 | 117.5 M |
| fuzzy | 205 | 115.1 M |
| malfunction | 195 | 98.1 M |
| replay | 210 | 102.2 M |
| none | 20 | 11.5 M |

* **By timing** (sessions): simultaneous 220, 0.5 s 160, 2 s 30, 10 s 30, partial 30, bounded 0.5/2/10 s 30
  each, probe 15, isolated 60, S2 near-simultaneous 90, S2 staggered 0.5 s 60, S2 separated 10 s 30, none 20.
* **Totals:** 346.8 vehicle-hours.
* **Storage breakdown:** metadata and validation 0.54 GB; routes and configs 0.11 GB.
* **Model comparison:** the M9.1 model predicted 448.1 M frames (actual −0.8 %) and 18.7 h. Actual generation took
  25.8 h, because K = 50/100 flooding and fuzzy campaigns ran slower than the extrapolated model.

## Resources

Peak RSS by N (min / median / max):

| N | Min | Median | Max |
|---|---|---|---|
| 10 | 424 MB | 501 MB | 580 MB |
| 20 | 520 MB | 608 MB | 859 MB |
| 50 | 650 MB | 742 MB | 1,236 MB |
| 100 | 710 MB | 808 MB | 1,774 MB |

* **Maximum:** 1,774 MB, at `FCS10_N100_S3_flooding_K100_bounded_spread_2s_R2` (1.50 M frames).
* **Memory stayed bounded.** The longest sessions (60 s, about 1.93 M frames at N = 100) peaked at about
  0.95 GB. Peak RSS follows the size of the concurrent campaign state, not accumulated frame history.
* **Other resources:** at most 109 threads and 314 open file descriptors; temporary staging at most 171 MB.
* **Disk:** at least 45.92 GB stayed free throughout (hard stop was 15 GB).

## Freeze and integrity

* **Component files:** `data/final/fleetcan-sim-scale-1.0/SHA256SUMS` covers 26,740 files, 21,727,934,640 bytes,
  across all six session directory types for all 835 sessions plus the final directory.
* **SHA-256 values:**

| File | SHA-256 |
|---|---|
| SHA256SUMS | `5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70` |
| FREEZE.json | `c714b479…f740` |
| manifest.jsonl | `e5138551…12c6` |

* **Checksum verification:** `sha256sum -c` passed.
* **Read-only:** writable component files after the freeze: 0.
* **Provenance snapshot:** contains the matrix (frozen SHA verified), the M9.1 freeze manifest, the expected
  sessions, profiles, fleets, the version file, the ledger, the execution log, checkpoints and the environment.
* **fleetcan-sim-1.1 after M10:** 20,805/20,805 OK, all hashes unchanged, 610 sessions, 0 writable — PASS. The
  baseline freeze record is unchanged and kept separate.
* **Matrix after M10:** SHA unchanged; the M9.1 validator passes.

## Reproducibility (`scripts/m10_finalize.py repro`)

Seven frozen sessions were regenerated under `M10_REPRO_*` IDs with the same configuration and seed:

* N20 S0;
* N50 S1 replay;
* N100 S2 flooding;
* N100 K100 replay;
* N100 K50 bounded spread;
* N100 mixed flooding;
* G0 fuzzy.

All raw and ground-truth tables were IDENTICAL, including 161,799 replay links. Seed, fleet, route SHA, campaign
schedule and runtime attacks were also equal. The comparison excludes only session IDs, frame UIDs and host
capture/wall-clock times, as defined earlier. The duplicates were deleted afterwards.

## Tests

* **Before generation:** 287 passed.
* **After generation:** 291 passed, including new tests for the runner (`tests/test_m10.py`).

Infrastructure test and validator changes, all explicitly approved behaviours:
* The M9.1 validator's "no consumer" check allows the authorised M10 runner.
* The pre-generation collision gates recognise sessions tagged with this matrix's ID and SHA as its own output.
* The raw-only completion mode was added to `session_state.py`; its default is unchanged.

No test was changed to accept scientific output.

## Git

| Commit | Content |
|---|---|
| `f212877` | runner |
| `7b65963` | raw-only scope |
| `3a2b53c` | finalisation fixes |
| (this commit) | results and report |

The dataset itself lives in `data/`, which is git-ignored; only scripts, docs and small result records are
versioned. Run records are in `results/m10_generation/`.

## Limitations

* Generation took 25.8 h against the modelled 18.7 h; the K = 50/100 flooding and fuzzy attack load was
  extrapolated.
* The 116 early sessions record a different runner commit from the rest. The generation code is identical, and the
  sessions are bit-reproducible (shown by the repro checks, which include pilot sessions from that commit).
* The recorded `simulation.sumo_config` path points to the staging location. The route content is pinned by
  `route_sha256` and verified identical in the repro checks.
* Per-session generator logs remain in `data/m10_generation/logs/` and are not part of the frozen component.
* No descriptors are included; FLEET-GUARD derives them from `raw/` according to its own pipeline.
* Leakage caution: `raw/<sid>/can_frames` contains `scenario_id` (schema 1.0, unchanged from the baseline). It must
  be excluded from any detector input.
