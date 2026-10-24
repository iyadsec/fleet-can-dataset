"""M12: build the synthetic vehicle profiles D-H for fleetcan-sim-2.0 (DBC files + profiles YAML).

Deterministic (seed 12_2026): re-running reproduces byte-identical files. Each profile picks 8-16 message templates
from a catalogue, its own CAN-ID allocation, periods, phases, message modes (periodic / event / mixed), release
jitter and sensor-noise levels. Profiles A-C (config/profiles/profiles_abc.yaml) are unchanged and reused.
All layouts are SYNTHETIC and defined by this project; none is taken from an OEM.

Usage: .venv/bin/python scripts/fcs2_make_profiles.py   -> dbc/fcs2_profile_{d..h}.dbc, config/fcs2/profiles_d_h.yaml
"""

from __future__ import annotations

import copy
from pathlib import Path

import cantools
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
SEED = 122026

# signal templates: name -> (quantity, bits, signed, scale, offset, min, max, unit)
SIG = {
    "rpm": ("engine_rpm", 16, False, 0.25, 0, 0, 16383.75, "rpm"),
    "torque": ("engine_torque_nm", 12, False, 0.5, 0, 0, 2047.5, "Nm"),
    "throttle": ("throttle_pct", 8, False, 0.4, 0, 0, 102, "%"),
    "speed": ("speed_kmh", 16, False, 0.01, 0, 0, 655.35, "km/h"),
    "accel": ("accel_mps2", 16, True, 0.001, 0, -32.768, 32.767, "m/s^2"),
    "wfl": ("wheel_fl_kmh", 16, False, 0.01, 0, 0, 655.35, "km/h"),
    "wfr": ("wheel_fr_kmh", 16, False, 0.01, 0, 0, 655.35, "km/h"),
    "wrl": ("wheel_rl_kmh", 16, False, 0.01, 0, 0, 655.35, "km/h"),
    "wrr": ("wheel_rr_kmh", 16, False, 0.01, 0, 0, 655.35, "km/h"),
    "brake_on": ("brake_active", 1, False, 1, 0, 0, 1, ""),
    "decel": ("brake_decel_mps2", 16, False, 0.001, 0, 0, 65.535, "m/s^2"),
    "angle": ("steering_angle_deg", 16, True, 0.1, 0, -3276.8, 3276.7, "deg"),
    "yaw": ("yaw_rate_dps", 16, True, 0.01, 0, -327.68, 327.67, "deg/s"),
    "lat": ("lat_accel_mps2", 16, True, 0.001, 0, -32.768, 32.767, "m/s^2"),
    "pedal": ("pedal_pct", 8, False, 0.4, 0, 0, 102, "%"),
    "gear": ("gear", 4, False, 1, 0, 0, 15, ""),
    "coolant": ("coolant_temp_c", 8, False, 1, -40, -40, 215, "C"),
    "oil": ("oil_temp_c", 8, False, 1, -40, -40, 215, "C"),
    "ambient": ("ambient_temp_c", 8, False, 0.5, -40, -40, 87.5, "C"),
    "batt": ("battery_v", 16, False, 0.001, 0, 0, 65.535, "V"),
    "fuel": ("fuel_pct", 8, False, 0.4, 0, 0, 102, "%"),
    "odo": ("odometer_km", 24, False, 0.1, 0, 0, 1677721.5, "km"),
    "turn": ("turn_signal", 2, False, 1, 0, 0, 3, ""),
    "head": ("headlight", 1, False, 1, 0, 0, 1, ""),
    "lock": ("door_locked", 1, False, 1, 0, 0, 1, ""),
    "disp_speed": ("speed_kmh", 8, False, 1, 0, 0, 255, "km/h"),
    "disp_rpm": ("engine_rpm", 8, False, 32, 0, 0, 8160, "rpm"),
}
# message templates: name -> (sender, signals, counter?, mode, periods allowed, watch, threshold, role tags)
MSG = {
    "ENGINE": ("ECM", ["rpm", "torque", "throttle"], True, "periodic", (10, 20), (), 0, {"engine_rpm": "rpm", "accelerator": "throttle"}),
    "VEH_SPEED": ("ABS", ["speed", "accel"], True, "periodic", (10, 20), (), 0, {"vehicle_speed": "speed"}),
    "WHEEL_SPEEDS": ("ABS", ["wfl", "wfr", "wrl", "wrr"], False, "periodic", (20, 25, 50), (), 0, {}),
    "BRAKE": ("ABS", ["brake_on", "decel"], True, "mixed", (20, 50), ("brake_active",), 0.5, {"brake": "decel"}),
    "STEERING": ("EPS", ["angle", "yaw"], True, "periodic", (10, 20), (), 0, {"steering": "angle"}),
    "CHASSIS": ("ABS", ["lat", "yaw"], True, "periodic", (20, 50, 100), (), 0, {}),
    "PEDALS": ("ECM", ["pedal", "brake_on"], True, "periodic", (20, 50), (), 0, {}),
    "TRANSMISSION": ("TCM", ["gear"], True, "event", (500, 1000), ("gear",), 0.5, {}),
    "TEMPS": ("ECM", ["coolant", "oil", "ambient"], False, "periodic", (500, 1000), (), 0, {}),
    "BATTERY": ("BMS", ["batt"], True, "periodic", (100, 200, 500), (), 0, {}),
    "FUEL_ODO": ("CLU", ["fuel", "odo"], False, "periodic", (1000,), (), 0, {}),
    "BODY": ("BCM", ["turn", "head", "lock"], True, "event", (500, 1000), ("turn_signal", "headlight", "door_locked"), 0.5, {}),
    "CLUSTER": ("CLU", ["disp_speed", "disp_rpm", "fuel"], True, "periodic", (100, 200), (), 0, {}),
    "HEARTBEAT_ECM": ("ECM", [], True, "periodic", (100, 200), (), 0, {}),
    "HEARTBEAT_BCM": ("BCM", [], True, "periodic", (200, 500), (), 0, {}),
    "TURN_LAMP": ("BCM", ["turn"], True, "mixed", (500,), ("turn_signal",), 0.5, {}),
}
REQUIRED = ["ENGINE", "VEH_SPEED", "BRAKE", "STEERING"]      # cover the five semantic attack roles
OPTIONAL = [m for m in MSG if m not in REQUIRED]
PROFILES = {"D": 8, "E": 10, "F": 12, "G": 14, "H": 16}
JITTER = {"D": 0.005, "E": 0.01, "F": 0.02, "G": 0.03, "H": 0.05}
NOISE_SCALE = {"D": 0.5, "E": 1.0, "F": 1.5, "G": 2.0, "H": 3.0}
NOISE_BASE = {"engine_rpm": 4.0, "engine_torque_nm": 1.5, "throttle_pct": 0.3, "accel_mps2": 0.02, "brake_decel_mps2": 0.01,
              "steering_angle_deg": 0.3, "yaw_rate_dps": 0.05, "lat_accel_mps2": 0.02, "wheel_fl_kmh": 0.05,
              "wheel_fr_kmh": 0.05, "wheel_rl_kmh": 0.05, "wheel_rr_kmh": 0.05, "coolant_temp_c": 0.3,
              "oil_temp_c": 0.3, "battery_v": 0.03, "pedal_pct": 0.2}
POWERTRAIN = {"idle_rpm": 750, "wheel_radius_m": 0.32, "final_drive": 3.7, "gear_ratios": [3.5, 2.0, 1.35, 1.0, 0.8, 0.65],
              "upshift_speeds_kmh": [15, 32, 52, 72, 92], "max_accel_mps2": 2.6, "pedal_cruise_pct": 15.0,
              "brake_threshold_mps2": 0.3}


def layout(signals: list[str], counter: bool):
    """Little-endian packing in template order, byte-aligned for >= 8-bit signals; counter in the last nibble."""
    out, bit = [], 0
    for s in signals:
        q, bits, signed, scale, off, lo, hi, unit = SIG[s]
        if bits >= 8 and bit % 8:
            bit += 8 - bit % 8
        out.append((s, bit, bits, signed, scale, off, lo, hi, unit))
        bit += bits
    if counter:
        if bit % 8:
            bit += 8 - bit % 8
        out.append(("Counter", bit, 4, False, 1, 0, 0, 15, ""))
        bit += 4
    if not out:
        out.append(("Counter", 0, 4, False, 1, 0, 0, 15, ""))
        bit = 4
    dlc = max(1, -(-bit // 8))
    return out, dlc


def main():
    rng = np.random.default_rng(SEED)
    used_ids_global = set()
    yml = {"profiles": {}}
    for p, n_msg in PROFILES.items():
        names = REQUIRED + [OPTIONAL[i] for i in sorted(rng.choice(len(OPTIONAL), size=n_msg - len(REQUIRED), replace=False))]
        base = int(rng.integers(0x080, 0x500)) & ~0xF
        ids = sorted(int(x) for x in rng.choice(np.arange(base, min(0x7F0, base + 0x300), 4), size=len(names), replace=False))
        # higher-priority (lower) IDs to the fast messages, as a real allocation would
        rows = []
        for name in names:
            sender, sigs, counter, mode, periods, watch, thr, roles = MSG[name]
            period = int(rng.choice(periods))
            rows.append((period, name))
        rows.sort()
        dbc_msgs, msg_cfg, roles_cfg = [], [], {}
        senders = set()
        for (period, name), fid in zip(rows, ids):
            sender, sigs, counter, mode, periods, watch, thr, roles = MSG[name]
            sig_layout, dlc = layout(sigs, counter or not sigs)
            # DBC signal names are profile-specific (Pd_<name>) so profiles do not share text
            signals, sigmap = [], {}
            for (s, start, bits, signed, scale, off, lo, hi, unit) in sig_layout:
                sname = "Counter" if s == "Counter" else f"{p}{s.capitalize()}"
                signals.append(cantools.database.can.Signal(name=sname, start=start, length=bits, byte_order="little_endian",
                                                            is_signed=signed, conversion=cantools.database.conversion.LinearConversion(scale, off, False),
                                                            minimum=lo, maximum=hi, unit=unit))
                sigmap[sname] = "counter" if s == "Counter" else SIG[s][0]
            mname = f"{p}_{name}"
            dbc_msgs.append(cantools.database.can.Message(frame_id=fid, name=mname, length=dlc, signals=signals, senders=[sender]))
            senders.add(sender)
            offset = round(float(rng.uniform(0, min(period, 10))), 1)
            m = {"name": mname, "period_ms": period, "offset_ms": offset, "signals": sigmap}
            if mode != "periodic":
                m.update({"mode": mode, "watch": list(watch), "min_interval_ms": 20.0 if mode == "mixed" else 50.0,
                          "change_threshold": thr})
            msg_cfg.append(m)
            for role, s in roles.items():
                roles_cfg[role] = [mname, f"{p}{s.capitalize()}"]
        if "accelerator" not in roles_cfg:
            raise SystemExit(f"profile {p}: no accelerator role")
        db = cantools.database.can.Database(messages=dbc_msgs, nodes=[cantools.database.can.Node(s) for s in sorted(senders)])
        dbc_path = ROOT / "dbc" / f"fcs2_profile_{p.lower()}.dbc"
        text = db.as_dbc_string()
        text = text.replace('VERSION ""', f'VERSION "fleet-can-dataset fcs2_profile_{p.lower()} v2.0 (SYNTHETIC, not OEM)"', 1)
        dbc_path.write_text(text)
        cantools.database.load_file(str(dbc_path), strict=True)
        nscale = NOISE_SCALE[p]
        yml["profiles"][f"profile_{p}"] = {
            "description": f"Profile {p} (fleetcan-sim-2.0): {len(names)} synthetic messages, periods "
                           f"{sorted({r[0] for r in rows})} ms, jitter {JITTER[p]:.3f}, noise x{nscale}; SYNTHETIC, not OEM",
            "dbc": f"dbc/{dbc_path.name}", "nominal_bitrate": 500000,
            "bus": {"stuffing": "exact", "queue_limit": 32, "drop_policy": "drop_newest", "node_queue_order": "priority",
                    "expiry_policy": "deadline", "max_frame_age_ms": 100.0},
            "powertrain": {**copy.deepcopy(POWERTRAIN), "max_torque_nm": int(rng.integers(180, 420))},
            "chassis": {"wheelbase_m": round(float(rng.uniform(2.5, 3.1)), 2), "steering_ratio": round(float(rng.uniform(13.0, 17.0)), 1)},
            "jitter_fraction": JITTER[p],
            "noise": {q: round(s * nscale, 4) for q, s in NOISE_BASE.items()},
            "roles": roles_cfg, "messages": msg_cfg}
    out = ROOT / "config" / "fcs2" / "profiles_d_h.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# GENERATED by scripts/fcs2_make_profiles.py (seed %d). Do not edit by hand.\n" % SEED
                   + yaml.safe_dump(yml, sort_keys=False, width=140))
    print("written", out, [len(v["messages"]) for v in yml["profiles"].values()])


if __name__ == "__main__":
    main()
