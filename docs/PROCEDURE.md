# End-to-end procedure and component map

This document describes how the two frozen dataset components were produced, which code and configuration
produced them, where every record is kept, and how to verify or regenerate any session. It describes the
generation side only. Detection (FLEET-GUARD) lives outside this repository; the M7 baseline is the one exception.

## 1. The two dataset components

| | fleetcan-sim-1.1 (baseline) | fleetcan-sim-scale-1.0 (expanded) |
|---|---|---|
| Fleet sizes | N = 2, 5, 10 | N = 20, 50, 100 + G0 bridge at N = 10 |
| Sessions | 610 (122 configurations × 5) | 835 (167 configurations × 5) |
| Session IDs / seeds | `FC11_*` / 610001–611215 | `FCS10_*` / 920001–921665 |
| Generation spec | `config/final/matrix/matrix_spec.json` (sha256 `4e6dd99d…`) | `configs/fleetcan_sim_scale_1_0.yaml` (sha256 `9fc005de…e735`, matrix ID `fleetcan-sim-scale-1.0-matrix-v1`) |
| Protocol / design | `docs/final_protocol.md` | `docs/m9_expanded_dataset_design.md`, `docs/m9_1_matrix_freeze.md` |
| Generator | 1.1.0 (frozen SUMO routes `sumo/routes/fleet_{2,5,10}.rou.xml`) | 1.3.0 (generated routes `grid_loops_v1`, bounded memory) |
| Contents per session | raw, ground truth, metadata, validation, **24-D descriptors** (`analysis/`) | raw, ground truth, metadata, validation, routes, session config — **no descriptors** (FLEET-GUARD derives them) |
| Freeze (on the VM) | `data/final/fleetcan-sim-1.1/` | `data/final/fleetcan-sim-scale-1.0/` |
| Freeze records (in Git) | `results/datasets/fleetcan-sim-1.1/` | `results/datasets/fleetcan-sim-scale-1.0/` |
| SHA256SUMS / FREEZE.json | `bc523937…18c8` (20,805 files) / `df58000a…a160` | `5d5418b1…de70` (26,740 files, 21.73 GB) / `c714b479…f740` |
| Generation report | M6 (this file, §5; audit and reproducibility in the freeze records) | `docs/m10_expanded_dataset_generation.md` |

The dataset files are not in Git: `data/` is git-ignored because of its size (about 2.4 GB baseline + 21.7 GB
expanded). They are on the Ubuntu VM in `~/fleet-can-dataset/data/`. Git holds the code, configuration,
generation specifications, run records and the complete hash lists, so any copy of the data can be verified
byte-for-byte (§8).

## 2. Environment

| Item | Value (recorded in every session's `metadata.json`) |
|---|---|
| Host | Ubuntu 24.04 arm64 in a UTM VM (8 vCPU, 7.9 GB RAM). Disk enlarged to 100 GiB in M9.2 (`docs/m9_2_storage_capacity.md`) |
| Kernel | 6.8.0-90 (baseline run), 6.8.0-147 (expanded run) |
| Python | 3.12.3, venv `.venv`, packages pinned in `requirements.lock.txt` (`requirements.txt` for install) |
| SUMO | 1.28.0 (TraCI API 22), pip wheel `eclipse-sumo==1.28.0` |
| CAN transport | Linux SocketCAN vCAN, one interface per vehicle (`vcan0 …`) |
| Repositories | VM `~/fleet-can-dataset` (commits are made here) → Mac clone (`origin` = VM) → GitHub `iyadsec/fleet-can-dataset` via `scripts/sync_github.sh` |

Setup:

```bash
sudo apt-get install -y can-utils python3-venv git
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
scripts/verify_environment.sh 3                 # checks prerequisites, changes nothing
sudo scripts/setup_vcan.sh 100                  # vcan0..vcan99 (not persistent: repeat after every reboot)
scripts/test_vcan.sh 3
.venv/bin/python -m pytest -q tests             # 291 tests at the end of M10
```

## 3. Generation pipeline (code map)

```
SUMO (TraCI) -> VehicleState -> per-vehicle CAN generator (DBC + profile A/B/C)
  -> attack engine (flooding / fuzzy / malfunction / replay; campaigns from the scheduler)
  -> logical CAN bus model per vehicle (bit-exact frame length, ID arbitration, queue 32, drop_newest,
     100 ms deadline) -> SocketCAN vCAN -> capture -> recorder
  -> data/raw (observations)  |  data/ground_truth (labels, attacks, campaigns: separate)
  -> data/metadata (provenance, config snapshot, bus stats)  |  data/validation (V/B/A/C checks)
```

| Module | Role |
|---|---|
| `src/orchestrator.py` | runs one session; stages in `<root>/.incomplete/<sid>`, validates, publishes with metadata last, never overwrites |
| `src/config.py` | YAML loading; fleet construction (`balanced_shuffle` profiles, switch-on U[0, 1] s @ 1 µs, ±1 % ECU jitter); `derive_seed` |
| `src/sumo_controller.py` | SUMO via TraCI (10 ms step, integer-ns clock) |
| `src/route_generator.py` | `grid_loops_v1` generated routes for any N (generator 1.2.0+) |
| `src/can_generator.py` | SUMO state → signals → DBC frames (profiles in `config/profiles/profiles_abc.yaml`, DBCs in `dbc/`) |
| `src/attack_engine.py` | the four frozen attack families |
| `src/campaign_scheduler.py` | coordinated / independent campaigns, exact per-member timing |
| `src/bus_model.py`, `src/can_bus.py` | logical CAN bus model and SocketCAN transport / capture |
| `src/recorder.py`, `src/stream_recorder.py`, `src/chunked.py`, `src/bus_stats.py`, `src/session_groups.py` | dataset rows, bounded-memory streaming writer and group-wise processing (1.3.0) |
| `src/ground_truth.py` | ground-truth tables (separate from observations) |
| `src/metadata.py` | metadata: seeds, versions, Git commit and dirty flag, environment, config snapshot with SHA-256 |
| `src/validator.py` | automatic session validator (validation_report.json / .md) |
| `src/storage.py`, `src/version.py` | Parquet/CSV storage; schema 1.0 / generator 1.3.0 / descriptor 1.0 |
| `analysis/*` | descriptor layer (24-D, 100-frame windows, stride 50): used for fleetcan-sim-1.1 only |
| `detection/*`, `scripts/m7_run.py` | M7 local Isolation Forest baseline on fleetcan-sim-1.1 (`docs/m7_local_if_protocol.md`) |

Further detail: `docs/architecture.md`, `docs/dataset_schema.md`, `docs/timing_model.md`, `docs/descriptors.md`.

## 4. Milestone history

| Milestone | Result | Documentation / code |
|---|---|---|
| M1 | benign SUMO → TraCI → CAN → vCAN → capture → dataset pipeline | README, `config/experiment.yaml` |
| M2 | single-vehicle attacks (flooding, fuzzy, malfunction, replay) | `config/m2/`, `scripts/run_m2_suite.sh`, `scripts/summarize_m2.py` |
| M3 | multi-vehicle campaigns S0–S3 (coordinated, independent, mixed), logical bus model | `config/m3/`, `scripts/summarize_m3.py` |
| M4 | descriptor analysis layer (24-D, observable timestamps) | `analysis/`, `docs/descriptors.md`, `config/m4/` |
| M5 / M5.1 | profiles A/B/C, seeded assignment / switch-on / jitter, Parquet, fast mode, benchmarks | `config/m5*`, `config/m51/`, `scripts/m5_*`, `scripts/m51_*` |
| M6 | **fleetcan-sim-1.1** generated and frozen (generator 1.1.0) | `docs/final_protocol.md`, `scripts/m6_*.py`, `config/final/`, `results/datasets/fleetcan-sim-1.1/` |
| M7 | local Isolation Forest baseline on the baseline (the only detector work in this repository) | `docs/m7_local_if_protocol.md`, `detection/`, `results/m7_local_if/` |
| Audit | final dataset requirements audit (read-only) | `docs/final_dataset_requirements_audit.md`, `scripts/final_requirements_audit.py`, `results/final_requirements_audit/` |
| M8 | generator 1.2.0: generated routes, scalability to N = 100 | `docs/m8_*.md`, `scripts/m8_scaling.py`, `config/m8/`, `results/m8_scalability/` |
| M8.1 | generator 1.3.0: bounded memory, proven output-identical | `docs/m8_1_*.md`, `scripts/m81_*.py`, `config/m81/`, `results/m8_1_memory_scalability/` |
| M9 | expanded design (design B, 162 configurations) | `docs/m9_expanded_dataset_design.md`, `scripts/m9_design.py`, `scripts/validate_m9_matrix.py`, `configs/m9_expanded_matrix_proposed.yaml`, `results/m9_design/` |
| M9.1 | frozen matrix v1 (B + G0 bridge = 167 configurations / 835 sessions) | `docs/m9_1_matrix_freeze.md`, `scripts/m91_freeze.py`, `scripts/validate_m91_matrix.py`, `scripts/session_state.py`, `configs/fleetcan_sim_scale_1_0.yaml`, `results/m9_1_freeze/` |
| M9.2 | storage expansion (64 → 100 GiB, 67.86 GB free) | `docs/m9_2_storage_capacity.md`, `scripts/infra/m92_{inspect,expand}.sh`, `results/m9_2_storage/` |
| M10 | **fleetcan-sim-scale-1.0** generated and frozen (raw-only) | `docs/m10_expanded_dataset_generation.md`, `scripts/run_m10_generation.py`, `scripts/m10_finalize.py`, `results/m10_generation/`, `results/datasets/fleetcan-sim-scale-1.0/` |

## 5. Reproducing fleetcan-sim-1.1 (M6)

```bash
.venv/bin/python scripts/m6_build_matrix.py              # config/final/matrix/ (matrix_spec.json + per-run configs; committed)
.venv/bin/python scripts/m6_run_matrix.py --preflight    # load every run through the real code, no SUMO
sudo scripts/setup_vcan.sh 10
.venv/bin/python scripts/m6_run_matrix.py                # 610 runs: generation + descriptor analysis; never overwrites
.venv/bin/python scripts/m6_audit.py                     # audit.json (validation, completeness, coverage, statistics)
.venv/bin/python scripts/m6_finalize.py repro            # regenerate 3 sessions under REPRO_ ids, compare, delete
.venv/bin/python scripts/m6_finalize.py provenance       # snapshot protocol, profiles, DBCs, fleets, matrix, versions, git, env
.venv/bin/python scripts/m6_finalize.py hash             # SHA256SUMS
.venv/bin/python scripts/m6_finalize.py freeze           # FREEZE.json + read-only
```

Result: 610/610 validation PASS, frozen 2026-10-16T14:04:02Z at commit `1de9447`.

## 6. Reproducing fleetcan-sim-scale-1.0 (M9 → M10)

```bash
.venv/bin/python scripts/m9_design.py --calibrate                  # cost-model calibration on measured M8.1 sessions
.venv/bin/python scripts/m9_design.py --write                      # design A/B/C + proposal YAML (deterministic; identical bytes)
.venv/bin/python scripts/validate_m9_matrix.py
.venv/bin/python scripts/m91_freeze.py --check                     # rebuilds the frozen matrix byte-identically
.venv/bin/python scripts/validate_m91_matrix.py                    # 26 checks incl. generator preflight of 835 sessions
.venv/bin/python scripts/m91_cost_gate.py                          # cost + disk gate (needs >= 30.1 GB free)
sudo scripts/setup_vcan.sh 100
SHA=9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735
.venv/bin/python scripts/run_m10_generation.py --authorize-matrix-sha $SHA --pilot   # 9 representative sessions
.venv/bin/python scripts/run_m10_generation.py --authorize-matrix-sha $SHA --all     # resumable; skips COMPLETE sessions
.venv/bin/python scripts/run_m10_generation.py --authorize-matrix-sha $SHA --status
.venv/bin/python scripts/m10_finalize.py validate    # independent re-verification of all 835 sessions
.venv/bin/python scripts/m10_finalize.py stats
.venv/bin/python scripts/m10_finalize.py hash        # manifest.jsonl, provenance/, SHA256SUMS
.venv/bin/python scripts/m10_finalize.py freeze      # FREEZE.json + read-only
.venv/bin/python scripts/m10_finalize.py repro       # 7 sessions regenerated under M10_REPRO_ ids, compared, deleted
.venv/bin/python scripts/m10_finalize.py baseline    # fleetcan-sim-1.1 integrity
```

**Runner requirements.**
* Generation must run from a clean Git working tree. Each session records the commit and a `dirty` flag, and the
  runner refuses modified tracked files.
* The runner stages each session under `data/.m10_stage`. It publishes `raw`, `ground_truth`, `validation` and
  `generated_sumo`, then `metadata` last, and only after verification passes.
* The run records are `data/m10_generation/`: the ledger `session_status.csv`, `execution_log.jsonl`, checkpoints
  and per-session logs. Copies are kept in `results/m10_generation/`.

**Scope note.** During M10 the component was made raw-only by user decision. 116 sessions generated before the
change had their `data/analysis/<sid>` removed. Those sessions record runner commit `f212877`; the other 719
record `7b65963`. Generation code (`src/`) is identical in both.

**Regenerating one session** with the same configuration and seed, without touching the frozen component (this is
what `m10_finalize.py repro` does):

```bash
cp data/m10_configs/<sid>/experiment.yaml /tmp/e.yaml   # set experiment_id to a new id and output.root to "data"
.venv/bin/python -m src.orchestrator --config /tmp/e.yaml --session-id <new_id>
.venv/bin/python scripts/compare_sessions.py <sid> <new_id>   # logical comparison (excludes ids, frame uids, host capture times)
```

## 7. Seeds and determinism

The master seed per session comes from the matrix:
* fleetcan-sim-1.1: `610000 + 10·index + rep`.
* fleetcan-sim-scale-1.0: `920000 + 10·config_index + rep`.

Derived streams, each with its own tag:

| Stream | Derivation |
|---|---|
| SUMO | `--seed` |
| Route generation | `derive_seed(seed, "route_generation")` |
| Profile assignment | `derive_seed(seed, "profile_assignment")` |
| Switch-on | `derive_seed(seed, "switch_on")` |
| ECU jitter | `derive_seed(seed, "ecu_jitter")` |
| Attacks | `SeedSequence([seed, crc32(attack_id)])` |
| Campaigns | `SeedSequence([seed, crc32("campaign:" + id)])` |

The scaled component's campaign members, independent targets and isolated targets were drawn offline by the
matrix builder (streams `campaign_members/C_MAIN`, `independent_targets`, `isolated_target`). The results are
frozen as explicit targets in the matrix.

Logical content is bit-reproducible. Only host capture timestamps vary between runs; they are measured,
non-authoritative and never used as ground truth.

## 8. Verifying a copy of the data against Git

To download and restore fleetcan-sim-scale-1.0 from its GitHub release, see `docs/dataset_download.md`
(`scripts/download_fleetcan_sim_scale_1_0.sh`).

```bash
cd ~/fleet-can-dataset/data
sha256sum -c --quiet ../results/datasets/fleetcan-sim-1.1/SHA256SUMS          # 20,805 files
sha256sum -c --quiet ../results/datasets/fleetcan-sim-scale-1.0/SHA256SUMS    # 26,740 files
```

The paths in both lists are relative to `data/`.

## 9. What is intentionally not in Git

| Item | Location | Reason |
|---|---|---|
| Dataset files of both components | VM `data/raw`, `data/ground_truth`, `data/metadata`, `data/validation`, `data/analysis` (baseline), `data/generated_sumo`, `data/m10_configs` | size; verified via the committed SHA256SUMS |
| Per-session M10 generator logs | VM `data/m10_generation/logs/` (14 MB) | operational logs, not part of the frozen component |
| M7 large outputs (`*.parquet`, `*.joblib`) | VM `results/m7_local_if/` | size (`.gitignore`) |
| Engineering sessions (M5–M9.1 tests) | VM `data/*/M8_*`, `M81_*`, `M91_*` | engineering only, not research data |
| Virtual environment, caches | `.venv/`, `__pycache__/` | reproducible from `requirements.lock.txt` |

## 10. Cautions for consumers (FLEET-GUARD)

* `raw/<sid>/can_frames` (schema 1.0, both components) contains `session_id` and `scenario_id`. `scenario_id` is a
  scenario label and must be excluded from detector inputs.
* All labels, attack and campaign identities, configured times and coordinated flags are in `ground_truth/` only.
* Descriptors exist for fleetcan-sim-1.1 (`analysis/`, descriptor version 1.0). For fleetcan-sim-scale-1.0, the
  FLEET-GUARD pipeline computes descriptors itself from `raw/`.
* N ≤ 10 (baseline) and N ≥ 20 (expanded) differ in route source and generator version. The G0 bridge sessions
  (N = 10, generated routes) exist to measure that difference.
