# Vehicle profiles A / B / C (synthetic)

All three profiles encode the **same** SUMO-derived physical state with the same synthetic physical model. They
differ only where real vehicle families plausibly differ: CAN-ID allocation, message periods and phases, DLC,
signal packing/grouping, scaling and message set. They are **not** models of real vehicles or OEMs. Bus settings
are identical (500 kbit/s logical bus, exact stuffing, queue 32, priority order, 100 ms deadline).

| Profile | DBC | Messages (period, phase) | Notes |
|---|---|---|---|
| A | `dbc/generic_vehicle.dbc` | ENGINE_STATUS 10 ms (0.0), VEHICLE_SPEED 20 ms (1.0), ACCEL_PEDAL 20 ms (3.0), BRAKE_STATUS 20 ms (5.0), STEERING 10 ms (7.0) | 5 IDs, ≈ 350 frames/s |
| B | `dbc/generic_vehicle_b.dbc` | PCM_ENGINE 20 ms (2.0), EPS_STEER 10 ms (4.0), ESC_SPEED 10 ms (6.0), PEDAL_STATUS 50 ms (8.0) | 4 IDs, different allocation/scaling/grouping, ≈ 270 frames/s |
| C | `dbc/generic_vehicle_c.dbc` | ENGINE_DATA 10 ms (0.5), WHEEL_SPEEDS 20 ms (2.5), VEHICLE_DYNAMICS 20 ms (4.5), BRAKE_DATA 20 ms (6.5), STEERING_SENSOR 10 ms (8.5) | 5 IDs, per-wheel speeds, shorter DLCs, ≈ 350 frames/s |

Common properties: periodic transmission with ±1 % uniform release jitter per stream; ECU switch-on delay
U[0, 1] s (1 µs resolution, seeded) after the vehicle's first SUMO step; counters in every message; values clamped
to the DBC range (0 clamped values recorded in all sessions). Profiles are assigned by a seeded balanced shuffle,
so every fleet is balanced (|N_A − N_B| ≤ 1, etc.) and every coordinated campaign is profile-balanced (spread ≤ 1)
except in G0 (baseline first-K rule).

Measured characteristics (all 835 sessions): `results/vehicle_profile_summary.csv`, `results/can_id_statistics.csv`,
`results/payload_statistics.csv`, `results/dlc_statistics.csv`, `results/interarrival_statistics.csv`,
`reports/P1_benign_behavior.md`.

Profile is a property of the vehicle recorded in `metadata/<sid>/vehicles.csv`. It may be used to **route** a
descriptor to a per-profile model (as the original per-OEM models and M7 do); it must never be a feature.
