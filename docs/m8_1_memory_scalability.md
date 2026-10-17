# Milestone 8.1 — memory scalability for large-fleet generation (generator 1.3.0)

**Verdict: PASS** — N = 100 sessions of 120 s (and 180 s) run on the unchanged 7.9 GB VM with bounded memory;
every logical output is identical to the pre-optimisation generator. fleetcan-sim-1.1 unchanged.
Audit written before the change: `docs/m8_1_memory_architecture_audit.md`.

## 1. Baseline protection

Before (commit c5bf083) and after (this milestone): `sha256sum -c SHA256SUMS` 20,805/20,805 OK; SHA256SUMS `bc523937…`,
FREEZE.json `df58000a…`, manifest `aebfd8e0…` identical; 610 sessions; 0 writable baseline files. All M8.1 runs were
written under new session IDs (`M81_*`, `*_M81*`); frozen sessions were only read.

## 2. Root cause (measured)

Generator 1.2.0 kept every frame alive four times during the run (`TxFrame`, `BusTx` in `all_txs` and
`AttackContext._tx`, `TxRecord` in `tx_log`, `CapturedFrame` lists: ≈ 1.1 KB/frame), then built a dict + formatted
strings per frame (+1.1 KB/frame), converted whole tables to PyArrow, and ran the validator on whole-session pandas
string tables (≈ 2.3 KB/frame). The analysis step did the same in a second process (≈ 1.6 KB/frame).
At N = 100 / 40 s: orchestrator 5,564–5,926 MB, standalone validator 2,862 MB, analysis 1,993 MB
(all RSS figures in this document are MiB, i.e. kB / 1024; the audit document's 2,939 / 1,992 MB were kB / 1000).

## 3. Architecture change

Old: `frames → retained in RAM (4 copies) → associate → row dicts → whole-table Parquet → whole-session validation`.

New (bounded):

```
SUMO step → CAN generators → attack engine → per-vehicle CanBusModel → send (vCAN)
      ↓                                  ↓ (Replay sources only)            ↓
 states → ChunkedTable          AttackContext.watch_replay          capture deque per channel
                                                                            ↓ take()
 FrameStream: pair k-th sent ↔ k-th captured per channel → emit in send order (= frozen (tx_end, vehicle) order)
      → frame_uid counter → ChunkedTable(can_frames | frame_labels | attack_frames | replay_map)
 BusStatsAccumulator: exact integer bins, attack windows; queue delays spilled to per-channel int64 files
      ↓ end of run
 finalize: temp all-text Parquet → final typed ZSTD Parquet, one row group at a time
 validate (staging dir, per vehicle group of 5) → publish raw/ground_truth/validation/metadata (metadata last)
 analysis (per vehicle group of 5)
```

Bounded state: chunk buffers (≤ 50,000 rows per table), frames awaiting capture (< 1 step in fast mode; measured
maximum recorded in metadata `execution.recorder.max_frames_awaiting_capture`), Replay source windows of active replay
attacks, drops (attack-bounded), per-group validator/analysis tables, descriptor tables (frames / 50).

## 4. Files changed

| File | Reason | Scientific semantics affected? |
|---|---|---|
| `src/chunked.py` (new) | bounded table writer reproducing the frozen Parquet typing rule; row-group-wise finalisation | NO |
| `src/stream_recorder.py` (new) | incremental capture pairing, emission order, frame UIDs, GT rows, Replay UID lookup | NO |
| `src/bus_stats.py` (new) | incremental bus statistics (exact) | NO |
| `src/session_groups.py` (new) | filtered per-vehicle-group reads, memory release after a group | NO |
| `src/orchestrator.py` | streaming loop, staged atomic publication, `--chunk-rows`, no overwrite | NO |
| `src/attack_engine.py` | Replay keeps only selectable source frames (same predicate, same order) | NO |
| `src/can_bus.py` | captures consumable incrementally (`take`) | NO |
| `src/recorder.py`, `src/ground_truth.py` | per-frame row builders and tallies (old functions kept) | NO |
| `src/validator.py` | group-wise accumulation of every check | NO |
| `analysis/run_session.py`, `labels.py`, `descriptor_validator.py`, `temporal_alignment.py` | group-wise analysis | NO |
| `src/version.py` | generator 1.3.0 (memory/storage architecture change, semantics preserved) | NO |
| `tests/test_m51.py` | version pin accepts 1.3.0 | NO |
| `tests/test_m81.py`, `tests/reference/validator_1_2.py` (new) | tests; whole-session 1.2.0 validator as reference | NO |
| `scripts/m81_*.py`, `config/m81/` (new) | profiling, runs, equivalence, verification | NO |

## 5. Equivalence

| Scope | Reference | Result |
|---|---|---|
| N ≤ 10 | 10 frozen fleetcan-sim-1.1 sessions (S0, S1×2, S2×2, S3×2, mixed×2, probe; all 4 families) re-run | identical: all raw/GT tables, frame UIDs, replay links, descriptors, labels, bus_stats, metadata (except execution timing), all logical validation checks |
| Validator | 865 sessions (610 frozen + 255 M1–M8), 1.2.0 whole-session validator vs 1.3.0 grouped | 865/865 identical checks, details and statistics (group sizes 1/3/5 also tested) |
| Analysis | 833 sessions, stored outputs vs grouped analysis | 833/833 identical descriptors, labels, temporal alignment, descriptor validation |
| N = 20/50 | M8 (1.2.0) sessions: S0 N20/N50, campaign N20 (20 members, 0.5 s), campaign N50 (10 members, 2 s), S2 N50 | identical |
| N = 100, 40 s | M8_S0_N100 (1.2.0), 1,277,152 frames | identical |
| N = 100 attacks | M8 flooding / fuzzy / malfunction / replay on A/B/C vehicles; 100-member replay campaign | identical (161,801 replay links) |
| Determinism | N = 100 40 s repeat; 80/120/180 s and long campaign repeated (before/after memory release) | identical |
| Chunk size | 10,000 vs 50,000 vs 100,000 rows (N = 20 replay campaign); 7 vs 1,000,000 in tests | identical |
| CAN isolation | N = 20 benign vs vehicle_001 over-saturated (5,283 queue drops, 1,720 expired) | other 19 vehicles bit-identical |

Only differences anywhere: execution timing, the per-session path of the generated SUMO scenario, and capture-layer
(host wall-clock) delays in C05 / `campaign_delay_by_layer` — documented as non-authoritative.

## 6. Memory (peak RSS)

Benign S0, 40 s (orchestrator incl. in-process validation; analysis is a separate process):

| N | frames | old peak (1.2.0) | new peak (1.3.0) | reduction | old analysis | new analysis |
|---|---|---|---|---|---|---|
| 10 | 128,667 | 769 MB | 569 MB¹ | 26 % | 417 MB | 367 MB¹ |
| 20 | 254,248 | 1,331 MB | 679 MB¹ | 49 % | — | 483 MB¹ |
| 50 | 637,046 | 2,942 MB | 822 MB¹ | 72 % | 1,089 MB | 596 MB¹ |
| 100 | 1,277,152 | 5,564 MB (repeat 5,926) | **782 MB** | **86 %** | 1,993 MB | 586 MB |

¹ first series, before the per-group memory release (N = 100 was 991 MB in that series).
Old standalone validator (whole session; inside the old orchestrator peak): 484 MB (N = 10), 1,555 MB (N = 50), 2,862 MB (N = 100).

N = 100 duration scaling (final code):

| Duration | frames | peak RAM | KB/frame | analysis peak | descriptors | wall | validation |
|---|---|---|---|---|---|---|---|
| 40 s | 1,277,152 | 782 MB | 0.61 | 586 MB | 25,397 | 162 s | PASS / PASS |
| 80 s | 2,571,554 | 883 MB | 0.34 | 752 MB | 51,285 | 324 s | PASS / PASS |
| 120 s | 3,865,952 | 1,056 MB | 0.27 | 801 MB | 77,173 | 480 s | PASS / PASS |
| 180 s | 5,807,553 | 1,414 MB | 0.24 | 1,029 MB | 116,005 | 743 s | PASS / PASS |

Generator 1.2.0 would have needed ≈ 17 GB (orchestrator) and ≈ 6 GB (analysis) at 120 s. During the simulation loop
RSS stays at 270–335 MB for 120 s; the remaining slow growth is post-loop and comes from state that legitimately grows
with session length: the full stream of one vehicle group (5 vehicles) during validation, the descriptor tables
(frames / 50) and one channel's queue-delay array at a time. Classification: **slow-growing with required state**,
no longer proportional to all historical frames (marginal cost ≈ 0.1–0.18 KB/frame vs 4.4 KB/frame before).
No OOM, no swap growth (0 MB), no watchdog kill; lowest MemAvailable 5.2 GB (final series).

## 7. Long and large campaigns

* N = 100, coordinated fuzzy, 10 members, 10 s stagger, full 110 s (NOT shortened): 3,592,146 frames, peak 1,059 MB,
  delays 0, 10, …, 90 s exact, generator/descriptor PASS, 0 loss; independent verification: 0 jitter mismatches,
  9,655 descriptors reconstructed (max relative error 2.5e-15).
* N = 100, 100-member simultaneous replay: identical to the M8 (1.2.0) session, peak 1,336 MB.

## 8. Replay

Replay selects benign frames of its source bus with `w0 <= tx_end < w1` (ID filter) in bus order. The engine now
offers each transmission to the Replay attacks watching that bus as it is transmitted; the attack keeps exactly the
frames the old full-history scan selected, in the same order (unit test against the old scan). The recorder remembers
the frame UID of a registered source only until its replay frame is written. Source → replay links identical in every
comparison (e.g. 161,801 links in the 100-member campaign).

## 9. Crash safety and temporary storage

Sessions are assembled in `data/.incomplete/<session>/` and validated there; directories are then moved into place
with metadata last. An interrupted run leaves only the staging directory (never a session with metadata), is
cleaned up by a re-run of the same ID, and an existing session is never overwritten (tested). Temporary storage peak
for N = 100: 107 MB (40 s) … 531 MB (180 s).

## 10. Remaining limits (measured)

* **Runtime/CPU (now the binding constraint):** RTF ≈ 0.25 at N = 100 (wall ≈ 4 × simulated; 743 s for 180 s). The main
  loop is one Python process: mean CPU 140 % of 8 cores (SUMO + capture threads), peak ≈ 420 % during finalisation.
* Disk: ≈ 1.56 MB per simulated second at N = 100 (281 MB for 180 s), plus temporary storage up to ≈ 0.53 GB.
* FDs 212–314 of 1,024; threads 109; vCAN 100 interfaces — not limiting.
* Comparison/diagnostic scripts written for M5–M8 (`compare_sessions.py`, `m8_scaling.py verify`) still load whole
  sessions; bounded alternatives `m81_memory.py compare-stream` / `verify-long` were added.

## 11. Reproduction

```
cd ~/fleet-can-dataset
.venv/bin/python -m pytest -q tests
.venv/bin/python scripts/m81_memory.py regress FC11_N10_S0_none_none_none_R1 ...        # + compare FC11_... M81_REG_FC11_...
.venv/bin/python scripts/m81_validator_equivalence.py --all-frozen --workers 4
.venv/bin/python scripts/m81_analysis_equivalence.py --all-frozen --workers 4
.venv/bin/python scripts/m81_memory.py configs
.venv/bin/python scripts/m81_memory.py run M8_S0_N100 --suffix _M81R
.venv/bin/python scripts/m81_memory.py run M81_S0_N100_D080 M81_S0_N100_D120 M81_S0_N100_D180 M81_CAMP_N100_fuzzy_s10_large --suffix _R
.venv/bin/python scripts/m81_memory.py compare-stream M81_S0_N100_D180 M81_S0_N100_D180_R
.venv/bin/python scripts/m81_memory.py verify-long M81_S0_N100_D120_R M81_S0_N100_D180_R M81_CAMP_N100_fuzzy_s10_large_R
cd data && sha256sum -c --quiet final/fleetcan-sim-1.1/SHA256SUMS
```
Results: `results/m8_1_memory_scalability/` (`memory_metrics.csv`, `before_reference.txt`, `compare/`, `verify/`,
`validator_equivalence.jsonl`, `analysis_equivalence.jsonl`, `profile_*.json`, `logs/`).
