# P2 — Replay timing and ECU activation

**Result: PASS.** All 3,065 replay instances in 210 sessions were checked. Every replay link is valid, every
configured-to-observable onset discrepancy is explained, and no labelling defect was found
(`validation/P2_replay_validation.csv`: 3,065/3,065).

## Generator model (`src/attack_engine.py`, Replay, generator 1.3.0)

A replay attack with configured start `s`, window `[w0, w1)` and offset 0.25 ms re-sends every benign frame of the
source bus with `w0 ≤ tx_end < w1`, in bus order. Each frame is generated at `s + (tx_end − w0) + 0.25 ms`. The
window is `[s − 5 s, s)`, or `[s − 10 s, s)` for partial overlap. The source is the vehicle's own bus.

**ECU activation is modelled explicitly.** Each vehicle's ECUs switch on `switch_on_ns` ~ U[0, 1] s (1 µs
resolution, seeded) after the vehicle's first SUMO step. Both values are in `metadata/<sid>/vehicles.csv`, together
with `ecu_activation_ns`, their sum on the session timeline. No frame exists before activation.

## Per-instance timeline (`results/replay_activation_timing.csv`)

Each instance records:
* configured start;
* source ECU activation;
* replay window start and end;
* first eligible source frame (index and time);
* predicted first injection;
* the generator's recorded first injection (`first_generated_ns`);
* first injected generation time;
* first observable replay frame (index and `timestamp_ns`);
* the delays, split into availability delay (first eligible frame − `w0`) and bus delay.

## Link validation (`results/replay_link_validation.csv`)

For every instance:
* every replayed frame links to an existing original frame on the same bus, with the same CAN ID and payload;
* the time shift equals `s − w0 + 0.25 ms` exactly (integer ns);
* all originals are benign and earlier than `s`;
* replayed frames are labelled malicious with the right attack ID;
* the set of linked originals equals the set of eligible source frames;
* the generator's first injection equals the prediction from the first eligible frame.

**3,065/3,065 pass.**

## Discrepancies and classification

| Class | Instances | Sessions | Configured → observable onset | Explanation |
|---|---|---|---|---|
| Expected replay behaviour | 3,055 | 210 | 0.43–8.49 ms (median 2.1 ms) | First benign source frame after `w0` (message phase ≤ the period of the first message, plus jitter), plus 0.25 ms offset, plus bus time (≈ 0.25 ms) |
| Source-frame availability delay | 10 | 10 | 144.6–980.6 ms | Partial-overlap first member (S3, `partial`): the replay window starts at `w0 = 0`, before the source ECU switches on (activation 0.14–0.98 s). The first eligible frame therefore appears only after activation, and replay starts that much later. |
| Expected simulation behaviour (other) | 0 | — | — | — |
| Ground-truth interpretation issue | 0 | — | — | — |
| Genuine labelling defect | 0 | — | — | — |
| Unresolved inconsistency | 0 | — | — | — |

* **Partial-overlap cases.** All 50 partial replay instances (10 sessions) were examined. Only the first member of
  each session is affected; members 2–5 have windows starting at ≥ 5 s, after every ECU is active.
* **Why the labels are correct.** No frame is labelled malicious before the first injected frame, and configured
  times are unchanged. The difference is between *configured* onset (ground truth) and *observable* onset (derived).
  Temporal analyses must use `derived_first_malicious_timestamp_ns` (issue FR-09: explained, category D).

## Limitations

* For ordinary replay instances, ECU activation is not on the critical path. For the 10 first members it is the
  whole delay.
* Replayed frames are bit-identical to originals. Only the duplicated timing makes them anomalous, so at most 50
  of 100 frames in a window are replay frames. They reach the original majority label only through its tie rule.
