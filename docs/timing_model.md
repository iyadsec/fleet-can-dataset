# Timing model

Three distinct times are kept for every frame. They must never be confused:

| Time | Column(s) | Clock | Meaning |
|---|---|---|---|
| Generation | `timestamp`, `sim_time_ns` | SUMO timeline (ground truth) | when the ECU task / attacker scheduled the frame |
| Modelled transmission | `transmission_timestamp`, `tx_start_ns`, `tx_end_ns`, `queue_delay_us` | SUMO timeline, from the logical bus model | when the frame wins arbitration (`tx_start`) and completes incl. intermission (`tx_end`) |
| Capture | `capture_time_ns`, `capture_offset_us` | Linux kernel CLOCK_REALTIME | when the vCAN capture socket received it |

`queue_delay_us = tx_start − generation` (CAN queueing/arbitration, modelled).
`capture_offset_us = capture_time − (wall_anchor + tx_end)` (host/VM execution jitter, measured).
Later research can therefore separate **campaign propagation delay** (configured on the SUMO
timeline), **CAN queueing delay** (`queue_delay_us`) and **testbed jitter** (`capture_offset_us`).

## Master clock
SUMO time is the only ground-truth clock: `sim_time_ns = step_index × step_ns` (step 10 ms),
cross-checked against `traci.simulation.getTime()` every step (abort if > 1 µs apart).

## Frame generation
Each DBC message is a periodic ECU task: due at `t_ign + offset + k × period`, where `t_ign` is the
first SUMO step in which the vehicle exists. A frame due in `[t_k, t_k + step)` carries the SUMO
state at `t_k` (sample-and-hold). No period jitter yet. Attack frames are scheduled on the same
timeline by the attack engine.

## Logical CAN bus model (`src/bus_model.py`, one instance per vehicle)
Frames enter the model at their generation time and leave it with a modelled transmission interval.

**Modelled**
* Exact Classical CAN frame length: SOF, 11-bit ID, RTR, IDE, r0, DLC, data, CRC-15 (computed
  from the frame), bit stuffing over SOF..CRC (exact count), CRC delimiter, ACK slot+delimiter,
  EOF (7) and intermission (3). Duration = bits / bitrate (default 500 kbit/s, 2 µs per bit).
  Example: ID 0x000 with 8 zero bytes = 127 bits = 254 µs → at most 3,937 frames/s.
* One frame on the bus at a time (non-preemptive); arbitration only when the bus is free.
* Arbitration: among frames ready at that moment, lowest CAN ID wins.
* Per-node transmit queues (each DBC sender ECU, plus `ATTACKER`), ordered by priority (or FIFO),
  limited to `queue_limit` frames; overflow handled by `drop_newest` or `drop_oldest`.
* Bus utilization (busy time / time) per 100 ms bin and overall; queue delay; drops.

**Not modelled**
* Bit-level arbitration timing, synchronisation, sample point, propagation delay.
* Error frames, retransmission, error counters, error-passive/bus-off, overload frames.
* Simultaneous transmission of the same ID by two nodes (real CAN: bit error). Here they are
  serialised and counted as `same_id_contention` in `bus_stats.json`.
* Electrical layer, transceivers, ACK failure, remote frames, CAN FD.

Processing is incremental per SUMO step: after all frames ready before `t_k+step` are submitted,
arbitration decisions before `t_k+step` are final. Unit tests prove step-wise processing gives
exactly the same schedule as processing everything at once.

## Transmission to vCAN and capture
Frames are handed to vCAN in modelled bus order, each when the host clock reaches
`wall_anchor + tx_end_ns` (real-time pacing). ECU and attacker frames use different sockets.
A separate socket per interface captures them with kernel timestamps.

## What vCAN does and does not do
**Does:** kernel delivery between sockets on one interface, per-interface ordering, kernel
timestamps, can-utils/python-can compatibility.
**Does not:** anything time- or bus-related (bitrate, arbitration, load, errors). These come from
the logical model above, which is a **model**, not a physical-layer simulation.
