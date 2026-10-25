"""Version identifiers stored in every session's metadata (prevents silently mixing incompatible runs).

SCHEMA_VERSION      raw + ground-truth table layout and semantics (src/recorder.py, src/ground_truth.py)
GENERATOR_VERSION   generator behaviour (SUMO coupling, CAN model, bus model, attacks, campaigns)
DESCRIPTOR_VERSION  analysis layer (FLEET-GUARD 24-D descriptor, windowing, timestamps)
Bump the relevant version whenever a change could alter generated or derived data.
"""

SCHEMA_VERSION = "1.0"
GENERATOR_VERSION = "2.0.0"   # 1.1.0: N<k profile subset, optional ECU release jitter
                              # 1.2.0: optional generated SUMO routes for arbitrary N (M8); legacy path unchanged
                              # 1.3.0: bounded-memory streaming recorder / validator / analysis (M8.1); semantics unchanged
                              # 2.0.0: M12 profiles D-H, event messages, sensor noise, campaign controller, condition
                              #        triggers, shared rng scope, malfunction value saturation (fleetcan-sim-2.0)
DESCRIPTOR_VERSION = "1.0"
