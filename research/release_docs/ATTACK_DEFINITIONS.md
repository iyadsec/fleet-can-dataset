# Attack definitions (frozen; unchanged in this release)

| Family | Definition | Rate | Duration |
|---|---|---|---|
| Flooding | CAN ID 0x000, DLC 8, all-zero payload, injected on the victim's bus (wins arbitration) | 1600 frames/s | 5 s (10 s in `partial`) |
| Fuzzy | random ID 0x000–0x7FF, random DLC 0–8 and random payload (seeded per attack) | 1000 frames/s | 5 s (10 s) |
| Malfunction | the profile's vehicle-speed message injected with constant 250 km/h, **additive** (the genuine sender keeps transmitting) | 100 frames/s | 5 s (10 s) |
| Replay | the victim bus's own benign frames from the window [start − T, start) (T = 5 s; 10 s in `partial`) re-sent in original order with the original relative timing + 0.25 ms | source traffic rate | T + 0.25 ms |

Implementation: `src/attack_engine.py` (generator 1.3.0). Each attack draws from a NumPy generator seeded with
(session seed, crc32(attack_id)); the seed is recorded in `ground_truth/<sid>/attacks.*`.

**Semantics to keep in mind when interpreting detection results**
* Flooding at 1600 frames/s raises bus load to ≈ 48 % on a 500 kbit/s bus but does not saturate it: no benign frame
  was dropped or expired in any session (`results/bus_statistics.csv`). It does not represent bus-saturating DoS.
* Malfunction is additive at 100 frames/s; the genuine speed message continues. With 270–350 benign frames/s a
  100-frame window contains at most ≈ 28 malfunction frames, so **no malfunction window reaches the original
  majority label** (see `results/attack_family_summary.csv`, `instances_with_majority_window`).
* Replay frames are bit-identical copies of earlier own-bus frames; only their timing (duplicated sequences) is
  anomalous.
* Observable onset differs from configured onset (bus delay, message phase, and for replay the availability of
  source frames — see `reports/P2_replay_timing.md`). Configured times are ground truth; observable times are derived
  and stored separately (`derived_*`).

Per-instance measurements for all 12,180 attack instances: `results/attack_instances.csv`; per family:
`results/attack_family_summary.csv`.
