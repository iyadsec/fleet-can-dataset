# M13 — fleetcan-sim-2.0 generation and release report

Status: complete. Released 2026-10-25 as `dataset-fleetcan-sim-2.0` (GitHub release, latest). The design is in
[m12_fleetcan_sim_2_0_design.md](m12_fleetcan_sim_2_0_design.md).

## 1. Result

| Property | Value |
|---|---|
| Sessions | 645 = 129 configurations × 5 repetitions; all PASS |
| CAN messages | 461,017,690 (Normal 420,615,395; Attack 40,402,295) |
| Vehicle traces | 37,750 (fleet sizes 20, 50, 100; profiles A–H balanced) |
| Attacks | 8,480 (Malfunction 2,980, Flooding 1,975, Fuzzy 1,865, Replay 1,660); 0 attacks without attack messages |
| Campaigns | 1,665 (420 coordinated, 1,245 independent); 8,420 members |
| Procedures | 1,316 distinct |
| Condition-triggered attacks | 1,555 (111 reached the 20 s timeout) |
| Saturated malfunction values | 73 |
| Session length | 15.9–69.2 s of simulated time |
| Released files | 1,298 (CSV and Parquet per session, 6 tables, README, SHA256SUMS), 11,611,562,499 bytes |
| `SHA256SUMS` SHA-256 | `0fd37bb31233c235712791df96ebe3ccd0948e781fd9e934c3b737059794b8b1` |
| Archive | 8 parts, 11,580,902,409 bytes (`results/dataset_release/fleetcan-sim-2.0/`) |

## 2. Scenarios

| Scenario | Sessions |
|---|---|
| S0 normal | 15 |
| S1 isolated attack | 60 |
| S2 independent attacks (diverse procedures) | 60 |
| S2 hard negative: shared behaviour | 30 |
| S2 hard negative: coordinated-looking timing | 30 |
| S2 hard negative: both | 30 |
| S3 coordinated campaign (K = 2–100) | 375 |
| S3-mixed | 30 |
| S3-probe | 15 |

## 3. Generation

* Matrix `configs/fleetcan_sim_2_0.yaml`, built by `scripts/fcs2_design.py`, final SHA-256
  `0bb2be29dfce6046600d0161c2d1e58508930e731a10f186e5d6eacf82fcc6e4`.
* Runner `scripts/run_fcs2_generation.py` (sequential, resumable, ledger `session_status.csv`), 2026-10-24 17:31 to
  2026-10-25 21:42 UTC. The final sessions took 27.3 h of generation time (213 µs per message).
* Each session records its generator version and commit (`sessions.csv`: `Generator_Version`, `Generator_Commit`).
  343 sessions record the version string "1.3.0" because they were generated before the version constant was raised;
  their code is identified by the commit.

### Corrections during generation

Each correction changed only the sessions it affected; those sessions were regenerated, and the other sessions'
configurations are identical under the final matrix.

| Problem | Fix | Sessions regenerated |
|---|---|---|
| Strong (±15 %) variation pushed a malfunction value beyond the signal range | member variation clamped to the signal limit; malfunction offset/scale saturates at the DBC range and is counted (`Saturated_Values`) | 5 |
| Isolated replay with `source_vehicle: self` produced no attack messages, and the validator did not catch it | isolated replays name their source vehicle; new validator check A08 (every configured attack produces attack messages) | 15 |
| Two sessions were disturbed by vCAN tests run on the same host; validator V14 detected unexpected frames | sessions regenerated; tests are not run during generation | 2 |

## 4. Validation

* **Generator validator:** frame checks V01–V29, attack checks A01–A08, bus checks B01–B07 and campaign checks C00–C08.
  It includes condition triggers (V29) and attacks without messages (A08). All 645 sessions pass.
* **Export:** every session's CSV and Parquet file was read back and compared with the source. Message, attack and
  campaign counts agree across logs and tables.
* **Release:** the archive was verified while streaming, and again after a full re-download from GitHub. All 1,298
  files matched `SHA256SUMS`.

## 5. Coordination evidence (full dataset)

The evidence is the cosine similarity of standardised 24-dimensional per-attack traffic descriptors, computed over
attack pairs. These descriptors are an analysis tool only; they are not part of the dataset.

| Pair | Pairs | Median |
|---|---|---|
| members of the same campaign | 141,203 | 0.866 |
| independent attacks with different procedures | 2,601 | 0.546 |
| independent attacks sharing a procedure (hard negatives) | 1,650 | 0.838 |

Breakdowns:

* **By strength (same campaign, median):** identical 0.878, mild 0.883, strong 0.840. Identical and mild are not
  distinguishable at this level.
* **By family (same campaign vs different procedure):**
  * flooding 0.907 vs 0.589;
  * fuzzy 0.852 vs 0.471;
  * malfunction 0.830 vs 0.230;
  * replay 0.877 vs 0.746.
* **Delivery latency:** median 77–88 ms for each pattern, at most 853 ms. For condition triggers the median wait is
  0.01 s and the maximum 20.0 s.
* **Benign variety:**
  * profiles D–H have 7.4–12.5 distinct CAN IDs per window, against 4.0–5.0 for A–C;
  * message-ID entropy is 2.35–2.88, against 1.79–2.24 for A–C.

## 6. Compared with fleetcan-sim-dataset-1.0

| | dataset-1.0 | 2.0 |
|---|---|---|
| Sessions / messages | 835 / 444 M | 645 / 461 M |
| Fleet sizes | 10, 20, 50, 100 | 20, 50, 100 |
| Profiles | A–C, periodic only | A–H, event messages, sensor noise, 0.5–5 % jitter |
| Coordination | schedule and label | shared procedure, plus command with per-vehicle delivery latency |
| Hard negatives | timing only | behaviour, timing, both |
| Floods | below bus capacity | up to saturation (frame drops) |

The log format is unchanged. fleetcan-sim-dataset-1.0 and fleetcan-sim-scale-1.0 remain frozen and published.

## 7. Known limitations

* 4×4 grid road network.
* The logical bus has no error frames and no bit errors.
* Frame drops occur only under saturating floods.
* Behavioural variety is limited to the eight synthetic profiles.
* Identical and mild coordination strength are nearly indistinguishable in traffic.
* `S2_HardNegative_Both` is not separable from a campaign by construction and should be reported separately.
