# Milestone 8.1 — memory architecture audit (generator 1.2.0, measured BEFORE any change)

Baseline at start: commit `c5bf083`, clean tree, generator 1.2.0; fleetcan-sim-1.1 `sha256sum -c` 20,805/20,805 OK
(SHA256SUMS `bc523937…`, FREEZE.json `df58000a…`, manifest `aebfd8e0…`), 610 sessions read-only.

## 1. Reference memory (M8, generator 1.2.0, 40 s benign S0, same VM)

| N | frames | orchestrator peak RSS | validator standalone peak | analysis (descriptors) peak | wall |
|---|---|---|---|---|---|
| 10 | 128,667 | 769 MB | — | — | 18.9 s |
| 50 | 637,046 | 2,942 MB | — | — | 77.8 s |
| 100 | 1,277,152 | 5,564 MB (repeat 5,926) | **2,939 MB** (30 s) | **1,992 MB** (28 s) | 155.3 s |

The validator also runs inside the orchestrator (its peak is part of the 5.6 GB); analysis runs as a separate process
after generation (it was not sampled in M8). All three are proportional to the frames of the whole session.

## 2. Who owns the memory (tracemalloc, N = 20, 40 s, 254,248 frames; `results/m8_1_memory_scalability/profile_M81_PROF_N020.json`)

Python heap alive at the end of the simulation loop — **≈ 280 MB ≈ 1.1 KB per frame, every frame held 4 times**:

| Allocation site | Object | MB | per frame |
|---|---|---|---|
| `can_generator.py:175` | `TxFrame` (generated frame) + payload bytes | 42.7 | 2 blocks |
| `can_bus.py:101–102` | `CapturedFrame` + data bytes (capture lists, per channel) | 58.3 | 5 blocks |
| `bus_model.py:255` | `BusTx` (modelled transmission), kept in `all_txs` and in `AttackContext._tx` | 37.8 | 3 blocks |
| `orchestrator.py:152` | `TxRecord` in `tx_log` | 21.5 | 2 blocks |
| `sumo_controller.py:96` | `VehicleState` in `states_all` (160,000 states) | 11.6 | — |
| cantools `message.py:954` | encoded payloads | 9.8 | 1 block |

End-of-run record phase (all simultaneously alive):

| Phase | Additional Python heap | Note |
|---|---|---|
| `recorder.associate` → `pairs` | +15 MB | list of (TxRecord, CapturedFrame) |
| `recorder.build_frame_rows` → row dicts + formatted strings, label seeds, `uid_of` | **+282 MB** (`recorder.py:116` 143.6 MB dicts, `:82` 25.6 MB time strings, `:123` 24.6 MB label seeds) | ≈ 1.1 KB per frame |
| `build_state_rows` | +36 MB | |
| `storage._parquet_table` (PyArrow arrays, C++ heap) | not traced; RSS +300–450 MB per large table | full-table conversion |
| in-process `validator.validate` | traced peak 861 MB; pandas string frames of the whole session | ≈ 2.3 KB per frame standalone at N = 100 |

Totals: traced peak 861 MB at N = 20 vs. 1.33 GB RSS without tracing; the difference is PyArrow/NumPy buffers and
allocator fragmentation (not traced). Scaling to N = 100 / 120 s (≈ 3.8 M frames) would need ≈ 16 GB (orchestrator),
≈ 8.8 GB (validator), ≈ 6 GB (analysis).

Other holders (bounded, not proportional to session history): per-node bus queues (≤ 32 frames/node), `held` frames
(< 1 step), Malfunction `last_benign` (≤ 8 frames per stream), Replay `_queue` (only frames of its own source window),
campaign/attack objects, metadata.

**Unbounded (session-history) holders, in order of size:** row dicts / strings (record phase), validator DataFrames,
PyArrow full-table conversion, analysis DataFrames (separate process), `CapturedFrame` lists, `BusTx` lists
(`all_txs` and `AttackContext._tx`), `TxRecord` lists, `TxFrame`s kept alive by the above, `VehicleState` list,
drops list (attack-bounded), bus-statistics inputs (`all_txs`).

## 3. Selected design (generator 1.3.0) — to be implemented

* **Streaming record:** a frame row is formed as soon as its capture is paired with its send record, and written
  through a bounded chunk writer. Pairing is unchanged (k-th captured on a channel ↔ k-th sent, ID + payload check).
  Emission follows the global send order, which already equals the frozen sort key `(tx_end_ns, vehicle_id)`; the
  frame UID counter advances only for matched pairs → identical UIDs. A reorder point waits only for frames whose
  capture has not arrived (bounded by capture lag; in fast mode ≤ one step).
* **Chunked tables:** rows are buffered up to `chunk_rows`, flushed as row groups of a temporary all-text Parquet
  (or appended CSV); per-column "all int / all float" flags reproduce the frozen typing rule; finalisation converts one
  row group at a time into the final Parquet/ZSTD file (no full-table concatenation).
* **Replay history:** `AttackContext` keeps only transmissions that some Replay attack can select (same predicate:
  source vehicle, `w0 <= tx_end < w1`, benign, ID filter), in the same order; Replay consumes that list. Frame UIDs are
  remembered only for registered replay sources until their replay row is written.
* **Bus statistics:** incremental per-channel counters, exact integer busy-time bins, attack-window accumulators;
  queue delays spilled to per-channel temporary int64 files, loaded one channel at a time for the frozen statistics.
* **Ground truth:** attack counters and first/last generation incrementally; drops kept (bounded by attack load).
* **Validation and analysis:** per vehicle group (default 5 vehicles; reads filtered by vehicle / frame UID),
  accumulating every existing check — reports must be identical to the frozen reports.
* **Crash safety:** the session is written under `data/.incomplete/<session>/`, validated there and only then moved
  into `data/{raw,ground_truth,validation,metadata}/` (metadata last). Existing sessions are never overwritten.
