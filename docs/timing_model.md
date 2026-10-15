# Timing model

## Master clock
SUMO simulation time is the only ground-truth clock. The orchestrator counts steps and defines
`sim_time_ns = step_index × step_ns` (step 10 ms). Each step is cross-checked against
`traci.simulation.getTime()` (abort if they differ by > 1 µs). All vehicles share this one timeline.

## Frame schedule
Each DBC message of a vehicle is a periodic ECU task. A vehicle's tasks start ("ignition") at the
first SUMO step in which the vehicle exists, `t_ign`. Message *m* is due at
`t_ign + offset_m + k × period_m`. A frame due in step window `[t_k, t_k + step)` carries the
SUMO state reported at `t_k` (sample-and-hold). Milestone 1 has no period jitter, so the schedule
is exact on the SUMO timeline.

## Transmission and capture
With `realtime: true`, SUMO time 0 is anchored to host wall time (`wall_anchor_ns`) and each frame
is sent when the host monotonic clock reaches `anchor + sim_time_ns`. A separate socket per vCAN
interface captures frames with kernel timestamps.

* `sim_time_ns`: when the frame *should* exist on the synthetic timeline (ground truth).
* `capture_time_ns`: when the kernel delivered it to the capture socket (observation).
* `capture_offset_us = capture_time_ns − (wall_anchor_ns + sim_time_ns)`: testbed/runtime jitter.

Campaign propagation delays (later milestones) are defined on the SUMO timeline, so they can be
told apart from runtime jitter, which shows up only in `capture_offset_us`.

## What vCAN does and does not emulate
**Does:** kernel frame delivery between sockets on one virtual interface, ordering per interface,
kernel receive timestamps, can-utils/python-can compatibility.
**Does not:** bitrate or frame duration, arbitration between IDs, bus load limits, bit stuffing,
error frames, ACK, bus-off, electrical behaviour. `nominal_bitrate` in profiles is informational.
Bus utilisation can only be *estimated* from frame counts, and any such estimate must be labelled as one.

## Measured in each validation report
Per vehicle: frames, observed rate. Per stream: SUMO-timeline period conformity (exact) and capture
inter-arrival error. Capture offset distribution (mean, p50, p95, p99, max).
