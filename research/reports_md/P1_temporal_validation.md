# P1 — Temporal handling and observability

**Result: PASS.**
* Descriptor timestamps, ordering, window boundaries and tie-breaking are verified in all 835 sessions
  (`validation/P1_temporal_consistency.csv`: 835/835).
* The temporal unit tests pass (`tests/test_temporal_consistency.py`).
* Configured and observable timing are kept separate. Configured differences of ≥ about one window stride are
  observable at descriptor level; finer ones are not. This is a documented resolution limit (FR-11), not a defect.

## Observable time and descriptor rules

* **Observable timestamp:** `timestamp_ns` = source `tx_end_ns`, the end of the frame on the logical bus on the shared
  SUMO timeline. It is an integer in ns.
* **Not used:** configured attack or campaign times, generation time (`sim_time_ns`) and the host wall clock
  (`capture_time_ns`) are never used for features or descriptor timestamps.
* **Ordering:** per vehicle, `timestamp_ns` is strictly increasing in every session (0 ties in 48,150 traces), so
  the observable order is unique. Ties across vehicles (15.5 M, on different buses) are irrelevant because windows
  are per vehicle. The tie-break `frame_index` is implemented and tested.
* **Windows:** 100 frames, stride 50, starting at frame 0, with the trailing partial window dropped.
  * Windows: 8,814,858; the per-vehicle count equals ⌊(n − 100)/50⌋ + 1 in every trace.
  * The descriptor timestamp is the timestamp of the window's last frame, verified for every window.
  * Descriptor timestamps increase strictly per vehicle.
* **Window span (benign):** 0.282 s (profiles A/C) and 0.368 s (B). The stride is about 0.14 s and 0.19 s.

## Attack-level observability (`results/temporal_observability.csv`, 12,180 instances)

| Family | First malicious frame − configured start (median / max) | First window with ≥ 1 attack frame (median) | ≥ 5 frames (median) | First majority window (median) |
|---|---|---|---|---|
| Flooding | 0.25 / 0.50 ms | 13 ms | 16 ms | 44 ms |
| Fuzzy | 0.16 / 0.47 ms | 19 ms | 23 ms | 68 ms |
| Malfunction | 0.24 / 0.49 ms | 57 ms | 98 ms | none (max 28 frames/window) |
| Replay | 2.1 / 980.6 ms | 39 ms | 52 ms | 192 ms (tie rule: exactly 50/100) |

Every attack instance has observable malicious frames (12,180/12,180). The last malicious frame lies 0.1–9.8 ms
before the configured end, because of the rate grid; for replay the maximum is the 0.25 ms offset.

## Campaign-level observability (`results/campaign_timing.csv`, 575 coordinated campaigns)

| Pattern | Campaigns | Configured spread | Observable first-frame spread (median) | Descriptor-level spread (median) | Order agreement, frame / window |
|---|---|---|---|---|---|
| simultaneous | 220 | 0 | 0.16 ms | 37 ms (max 135 ms) | n/a (no configured order) |
| small (0.5 s steps) | 160 | 0.5–9.5 s | ≈ configured | ≈ configured | 1.000 / 1.000 |
| medium (2 s) / large (10 s) | 30 / 30 | 8 / 40 s | ≈ configured | ≈ configured | 1.000 / 1.000 |
| partial (5 s, 10 s attacks) | 30 | 20 s | 20.0 s | 19.99 s | 1.000 / 1.000 |
| bounded_spread 0.5 s (K = 20–100, gaps ≥ 5 ms) | 30 | 0.5 s | 0.50 s | 0.51 s | 0.999 / **0.960** |
| bounded_spread 2 s / 10 s | 30 / 30 | 2 / 10 s | 2.0 / 10.0 s | 2.0 / 10.0 s | 1.000 / 0.999–1.000 |
| probe (0.1 s replay, K = 2) | 15 | 0.1 s | 0.094–0.106 s | 0.068–0.166 s | 1.000 / 1.000 (15/15) |

Independent attacks (1,320 non-coordinated campaigns: S1, S2, S3-mixed independents) are kept distinct from
coordinated campaigns in every table. No member lacks observable evidence (`members_without_observable_frames` = 0).

## Interpretation

* **Configured vs observable onset.** For flooding, fuzzy and malfunction these differ only by bus time (< 0.5 ms).
  For replay they also differ by the phase of the first source frame. The 10 partial-overlap first members are the
  exception (see `P2_replay_timing.md`).
* **Descriptor-level timing.** This adds the window-completion delay: tens of ms, and up to about 0.18 s for
  malfunction.
* **"Simultaneous" campaigns.** They appear with a 37 ms (median) descriptor-level spread because each vehicle's
  window boundary falls at a different phase.
* **Finer configured differences.** Differences below about one stride (0.14–0.19 s) are only partly resolvable:
  * In bounded spread 0.5 s at K = 100, 4 % of member pairs (gaps 5 ms) appear out of order at window level.
  * The 0.1 s probe is resolved in all 15 sessions, but its descriptor-level spread varies between 0.07 and 0.17 s.
* **Implication.** Any fleet-level temporal-correlation claim must state these limits. Timestamps were not
  altered to make campaigns easier to correlate.
