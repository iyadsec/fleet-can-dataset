# Known limitations

## The data are synthetic

* fleetcan-sim-research-1.0 is **synthetic**. It contains **no genuine production ECU captures**.
* **SUMO does not natively generate CAN traffic.** SUMO supplies vehicle kinematics. CAN messages are generated
  from that state by a synthetic model with project-defined DBC files (profiles A/B/C, not OEMs).
* The **logical CAN bus model is not equivalent to a physical CAN bus**. It models bit timing, stuffing,
  arbitration, queues and deadlines. It has no error frames, retransmissions, bus-off, transceiver or bit-level
  effects, and no gateways. Each vehicle has one bus.
* SocketCAN vCAN carries the frames but has no bitrate. Bus timing comes from the model.
* **Real-world generalisation has not been established.** Absolute detection performance on this dataset must not
  be presented as performance on real vehicles.

## Properties that can create shortcuts (documented, not changed)

| Property | Measurement | Issue |
|---|---|---|
| Few benign IDs, strict periodicity | 4–5 IDs per profile; period CV 0.0082; benign rate std ≈ 0.03 frames/s across vehicles | FR-04 |
| Constant payload bytes | bytes 4–6 always 0 in profiles A and B | FR-04 |
| Profile differences | B: 4 IDs at 270 frames/s; A/C: 5 IDs at 350 frames/s; C has shorter DLCs | use per-profile models; never use profile as a feature |
| Predictable warm-up | ECU switch-on U[0,1] s, then flat traffic; every attack starts at ≥ 10 s | FR-03, FR-06 |
| Start-up windows | 98.6 % of first windows per trace are locally anomalous | FR-06 |
| G0 identity | members are always vehicle_01…05; 2-digit IDs | FR-12 |
| Absolute time | attack timing follows a small set of patterns from 10 s | FR-03 |

## Attack semantics

* **Flooding** is non-saturating: bus load rises to ≈ 48 %, with 0 dropped or expired frames.
* **Malfunction** is additive and adds at most 28 frames per window, so it has **no** majority-label windows.
* **Replay** reaches the majority label only through the tie rule (exactly 50 of 100 frames).
* Durations are fixed (5 s; 10 s for partial overlap). Rates are fixed per family (fuzzy 1000 frames/s,
  flooding 1600 frames/s).

## Temporal resolution

* A 100-frame window spans 0.28 s (A/C) or 0.37 s (B), with a stride of about 0.14/0.19 s.
* Configured timing differences below about one stride are only partly observable at descriptor level:
  * simultaneous campaigns show a descriptor-level spread of 37 ms (median);
  * in bounded spread 0.5 s at K = 100, 4 % of member pairs appear out of order;
  * the 0.1 s probe is resolved, but its descriptor-level spread varies between 0.07 and 0.17 s.
* For 10 partial-overlap replay first members, the observable onset is 0.14–0.98 s later than configured, because
  of ECU activation (FR-09).

## Coverage

* Fleet sizes are 20/50/100, plus 10 in G0 only, with 5 repetitions per configuration.
* Attack family × timing is covered by a balanced reduced factorial, not a full cross.
* The bus has no frame loss (0 drops in all sessions), so loss conditions are untested.

## Detector-side facts that are not dataset defects

* With the original percentile normalisation, about 20 % of benign windows are "strong" by construction (FR-05).
* The original detection threshold needs labelled validation attacks (FR-07).

## Provenance

* The source was generated under two runner commits (f2128775: 116 sessions; 7b659631: 719) with identical
  generation code.
* This release is derived: no observation, label, timing or configuration was changed.
