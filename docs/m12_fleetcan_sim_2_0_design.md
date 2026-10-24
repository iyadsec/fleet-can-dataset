# M12 — fleetcan-sim-2.0 design (behavioural and temporal coordination, benign variety)

Status: design, approved option B (2026-10-24). fleetcan-sim-scale-1.0 and fleetcan-sim-dataset-1.0 stay frozen.

## 1. Goals

| Gap in v1 | v2 mechanism |
|---|---|
| Coordinated attacks are distinguishable from independent ones only by label and schedule; every attack of a family uses identical parameters | **Attack procedures.** A procedure is sampled per campaign from a parameter space, and all members execute it, mapped to their own profile. Each independent attack samples its own procedure. |
| Coordination is a fixed timetable | **Campaign controller.** A command is issued at time T and delivered to each member with a seeded latency. Patterns are broadcast, waves, sequential, partial, bounded spread and condition-triggered (the member fires when its own vehicle state meets a condition). |
| Hard negatives match timing only | **2 × 2 hard negatives.** Labelled look-alikes in behaviour, in timing, and in both. |
| Benign traffic too regular | **8 profiles** with 8–16 messages each, mixed periods (10 ms – 1 s), event-driven messages, per-profile jitter, sensor noise, slowly varying quantities. |
| Flooding never saturates; malfunction always additive | Procedure space includes **saturating floods** (frame drops occur) and malfunction in `replace` mode. |
| Attacks always start at 10 s | Command time sampled per session. |
| G0 identity bias | No special fleets; seeded, profile-balanced member selection for every N; one ID format. |
| Replay window could precede ECU switch-on | Replay window start ≥ 2.0 s (switch-on ≤ 1 s). |

## 2. Benign traffic (generator 2.0.0)

* **Profiles A–C unchanged.** New profiles D–H have their own DBCs (project-defined, not OEM) and 8–16 messages each.
  * Periods are drawn from {10, 20, 25, 50, 100, 200, 500, 1000} ms.
  * Phases and ID allocations differ per profile.
* **New model quantities**, deterministic or seeded per vehicle:
  * lateral acceleration, per-wheel speeds with small seeded offsets, engine torque estimate, throttle;
  * coolant and oil temperature (warm-up curve), battery voltage (load-dependent), fuel level (consumption);
  * turn indicator (from yaw rate), headlight and door states (seeded rare events), odometer.
* **Message modes**, set per message:
  * `periodic` (as in v1);
  * `event`: sent when a watched quantity changes, with a minimum interval, plus an optional heartbeat period;
  * `mixed`: periodic, plus immediate frames on change.
* **Sensor noise:** additive Gaussian noise per quantity, with σ set per profile and a seeded per-vehicle stream.
  `speed_kmh` stays noise-free, so validator V17 remains exact.
* **Jitter:** a per-profile release-jitter fraction overrides the fleet default (range 0.5–5 %).

## 3. Attack procedures (sampled by the design script; the attack engine already supports them)

| Family | Procedure parameters (per procedure) |
|---|---|
| Flooding | CAN ID ∈ [0x000, 0x0FF]; rate ∈ {400, 800, 1600, 3000, 4500} msg/s (≥ 3000 saturates 500 kbit/s); DLC ∈ {2, 4, 8}; payload fixed pattern or random |
| Fuzzy | mode ∈ {id, payload, both}; ID range = sampled sub-range; rate ∈ [200, 1500]; DLC fixed or random; for mode payload: target role |
| Malfunction | target role ∈ {vehicle_speed, engine_rpm, accelerator, brake, steering}; mode ∈ {inject, replace}; strategy ∈ {constant, offset, scale, ramp} with sampled value or factor; rate ∈ {native period, 2×, 5×, 10×} (inject) |
| Replay | window length ∈ [1, 8] s; ID subset ∈ {all, 2–4 sampled roles}; time offset ∈ [0.1, 5] ms |
| All | duration ∈ [2, 10] s |

* **`procedure_id`** is a stable hash of the procedure parameters and is written to the ground truth.
* **Coordination strength (campaigns):**
  * `identical`: same parameters, plus a shared random stream for fuzzy and random payloads (rng scope = campaign);
  * `mild`: per-member ±5 % on rate and value, own random stream;
  * `strong`: per-member ±15 %, own random stream.

## 4. Campaign controller (timing)

* The command time is T ∈ [8, 20] s, sampled per session.
* Member start = T + pattern offset + delivery latency.
* Latency is log-normal per member (median 80 ms, σ_log 0.6, capped at 1 s), drawn from a seeded campaign stream.
* All values are static except `condition`, so they are computed in the scheduler and recorded exactly.

| Pattern | Offsets |
|---|---|
| broadcast | 0 for all members |
| waves | members split into 2–4 groups; group g at g × W, W ∈ {1, 3, 5} s |
| sequential | i × Δ, Δ ∈ {0.5, 2} s (K ≤ 20) |
| partial | i × 5 s, duration 10 s (K = 5) |
| bounded | W × i / (K − 1), W ∈ {0.5, 2, 10} s |
| condition | armed at T + latency; starts when the vehicle's own state meets the condition (speed ≥ v*, or a brake event), with a timeout of 30 s after which it starts anyway |

Condition triggers are resolved at run time by the attack engine, which receives each SUMO step's vehicle states. The
ground truth records arm time, trigger time and whether the timeout fired.

## 5. Scenarios and hard negatives

| Scenario | Content |
|---|---|
| S0 | benign |
| S1 | one isolated attack (own procedure) |
| S2-diverse | k independent attacks (k ∈ {5, 10}), own procedures, independent Poisson start times over [T, T + 30 s] |
| S2-lookalike-behaviour | independent attacks share one procedure; start times independent |
| S2-lookalike-timing | independent attacks with own procedures; start times follow a coordinated pattern (broadcast or sequential) |
| S2-lookalike-both | shared procedure **and** coordinated-looking timing; indistinguishable by construction (control) |
| S3 | one coordinated campaign: K, pattern, strength |
| S3-mixed | coordinated campaign plus independent attacks (own procedures, independent times) in the same fleet |
| S3-multi | two concurrent coordinated campaigns with different procedures (reserved; not in the first matrix) |

## 6. Matrix (target ≈ 160–170 configurations × 5 repetitions)

* **Fleet sizes:** N ∈ {20, 50, 100}, 8 profiles balanced; K ∈ {2, 5, 10, 20, 50, 100} with K ≤ N.
* **Coverage:**
  * S3 crosses N × K × pattern;
  * family and strength are assigned by a balanced rotation, so each (pattern, family) and (K, strength) pair occurs;
  * hard-negative cells mirror the S3 cells at K = 5 and K = 10.
* **Cost:** about 35–40 h of generation at the measured 127 µs/frame. The matrix is frozen with a SHA before
  generation, as in M9.1.

## 7. Ground truth additions (schema 2.0)

* **attacks:** `procedure_id`, `coordination_strength`, `arm_ns`, `trigger_ns`, `trigger_timeout`.
* **campaigns:** `controller_pattern`, `command_ns`, `procedure_id`, `strength`.
* **campaign_membership:** `delivery_latency_ns`, `pattern_offset_ns`, `arm_ns`, `trigger_ns`.
* **sessions:** `hard_negative_type` ∈ {none, behaviour, timing, both}.

The export keeps the v1 log format (`Timestamp, Vehicle_ID, Arbitration_ID, DLC, Data, Class, SubClass, Attack_ID,
Campaign_ID`) and adds `Procedure_ID` to `attacks.csv` and the timing columns to `campaign_members.csv`.

## 8. Validation

* **Validator V01–V27 extended:**
  * V12 checks periodic streams only; event streams are checked for minimum interval and heartbeat;
  * V10 uses the first periodic message;
  * new V28 checks the procedure ground truth;
  * new V29 checks condition triggers (no member frame before its trigger; trigger ≥ arm).
* **Pilot of about 30 sessions before freezing:**
  * descriptor-level cosine similarity is higher within campaigns than between independent attacks, except in the
    look-alike controls;
  * delivery latency and patterns are observable;
  * benign variety is measurably above v1;
  * cost per frame is re-measured.
