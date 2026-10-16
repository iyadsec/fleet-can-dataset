# Milestone 8 — scalability architecture audit (written BEFORE any code change)

Baseline: commit `1131b27`, working tree clean, generator 1.1.0, dataset fleetcan-sim-1.1 frozen
(`sha256sum -c` 20,805/20,805 OK; SHA256SUMS `bc523937…`, FREEZE.json `df58000a…`, manifest `aebfd8e0…`;
610 sessions, 0 writable files). VM: 8 vCPU, 7.9 GB RAM, `ulimit -n` 1024 (soft), `ulimit -u` 31,209,
`fs.file-max` unlimited, 22 GB free, 10 vCAN interfaces up.

Every place where the fleet size N enters the generator:

| # | Component | File | Current behaviour | N-dependent assumption | Needed for N ≤ 100? |
|---|---|---|---|---|---|
| 1 | SUMO scenario / routes | `sumo/scenarios/fleet_{2,5,10}.sumocfg`, `sumo/routes/fleet_*.rou.xml` | hand-written, one file per N; vehicles depart t = 0 from distinct start edges on 3 closed loops (perimeter clockwise P 12 edges, counter-clockwise R 12 edges, inner I 4 edges) | only N = 2, 3, 5, 10 exist | **yes** — deterministic generator (new path); legacy files untouched |
| 2 | SUMO network | `sumo/network/grid4x4.net.xml` (netgenerate 4×4) | 48 single-lane edges, 185.6–189.6 m, 13.89 m/s, 12 traffic lights; 8,973 lane-m | capacity | no — 100 cars × 7 m (4.5 m + 2.5 m minGap) = 700 m standing (7.8 %), ≈ 2.1 km moving at 1 s headway (≈ 24 %); P/R/I loops use disjoint edges. Feasible without redesign |
| 3 | SUMO vehicle IDs | route files | must equal fleet IDs (`orchestrator` raises otherwise) | — | generated from the fleet assignment |
| 4 | Vehicle insertion | route files `depart="0" departSpeed="0"` | one vehicle per start edge | > 28 vehicles cannot each get their own edge | yes — several vehicles per edge at spaced `departPos` |
| 5 | Vehicle IDs | `config.build_fleet` `id_pattern` (default `vehicle_{n:02d}`) | configurable pattern | `:02d` gives `vehicle_100` (still unique; lexicographic order no longer numeric) | use `vehicle_{n:03d}` in NEW configs only (pattern already configurable, no code change) |
| 6 | Profile assignment | `config.build_fleet` balanced_shuffle | cycle A,B,C,A,… then seeded permutation | none (any N) | no change; N = 100 → A 34, B 33, C 33 |
| 7 | Switch-on / jitter | `config`, `can_generator` | one draw per vehicle / per (vehicle, message) stream | none | no change |
| 8 | Channels | `config.assign_channels` `vcan{index}` | vehicle i → `vcan(i-1)` | interface names ≤ 15 chars (`vcan99` OK) | no change |
| 9 | vCAN creation | `scripts/setup_vcan.sh` | refuses N > 64 | 64 is an arbitrary M1 input guard (typo protection), not a kernel limit | yes — raise the guard (documented), measure creation time |
| 10 | vCAN check | `orchestrator.check_channels` | every configured channel must exist and be UP | none | no change |
| 11 | Sender sockets | `can_bus.SocketCanTransport` | one socket per (channel, node); node ∈ {ecu, attacker} | 1–2 fds per vehicle | measure |
| 12 | Capture | `can_bus.SocketCanCapture` | one socket + one daemon thread per channel | N threads (GIL; 50 ms recv timeout) | measure; keep unless incorrect |
| 13 | Fast-mode flow control | `orchestrator.wait_for_capture` | after every step waits until each channel captured all sent frames (5 s timeout, counted) | cost grows with N | measure; timeouts must stay 0 |
| 14 | Per-vehicle generator / bus | `orchestrator.run` dicts keyed by vehicle / channel | dynamic | none | no change; one `CanBusModel` per channel (per-vehicle bus) |
| 15 | Memory | `orchestrator` (`tx_log`, `all_txs`, `states_all`, `captures`) | whole session retained, rows built at the end | O(N × duration) | measure peak RSS; change only if unsafe |
| 16 | Frame UID | `recorder` `F{i:09d}` per session | 10⁹ frames per session | none (100 × 350 fps × 1 h = 1.3 × 10⁸) | no |
| 17 | Campaign scheduler / GT | `campaign_scheduler`, `ground_truth` | target lists; pairwise overlap rows (k(k−1)/2) | 100-member campaign → 4,950 overlap rows | no change |
| 18 | Descriptors | `analysis/windowing`, `descriptor_extractor` | per (session, vehicle) | none | no change |
| 19 | Validators | `src/validator.py`, `analysis/descriptor_validator.py` | per-vehicle loops; B04 arbitration check is a Python loop over frames | runtime O(frames) | measure |
| 20 | Metadata snapshot | `metadata.snapshot_configs` | copies sumocfg + `network/*.xml` + `routes/*.xml` next to the sumocfg | assumes the static `sumo/` tree | generated scenarios are written as their own `scenarios/ routes/ network/` tree under `data/generated_sumo/<session>/` so the snapshot logic works unchanged |
| 21 | Config schema | `config.load_experiment` | `simulation.sumo_config` required | static scenario only | add optional `simulation.route_generation` (new path); legacy configs unchanged |
| 22 | Shell / suites | `scripts/run_suite.sh`, matrix scripts | iterate config files | none | no change |
| 23 | Searches | `grep` for `[-2:]`, `split("_")`, `zfill`, `:02d`, `range(10)`, `vcan9` | only `id_pattern` default `vehicle_{n:02d}` and `setup_vcan.sh` 64 | — | covered above |

**Decision summary (before implementation).**
* The 4×4 network can hold 100 vehicles without redesign → keep it (no STOP condition).
* Legacy path (fleetcan-sim-1.1 configs with `sumo_config`) is not touched; the new path is selected only by
  `simulation.route_generation`. Generator version → 1.2.0 (infrastructure added; legacy semantics unchanged).
* Route generator = the legacy mobility philosophy made parametric: the same three loops, `car_a` vType,
  `depart=0`, `departSpeed=0`; start positions = N slots drawn without replacement from 30 m-spaced slots on all loop
  edges (168 slots), seeded by `derive_seed(seed, "route_generation")` (new independent stream).
* Everything in sections 2 of the M8 specification (CAN, attacks, timing, descriptors, storage) stays as is.
