"""Unit tests: config, channel mapping, DBC encoding, generator timing, recorder, ground truth."""

import pytest

from src.can_bus import CapturedFrame
from src.can_generator import SyntheticVehicleModel, VehicleCanGenerator, load_dbc
from src.config import ConfigError, assign_channels, load_experiment
from src.ground_truth import frame_label_rows
from src.recorder import TxRecord, associate, build_frame_rows, fmt_time
from src.sumo_controller import VehicleState

CFG = load_experiment("config/experiment.yaml")
PROFILE = CFG.profiles["model_A"]


def state(t_ns, speed=10.0, accel=0.0, angle=90.0, vid="vehicle_01"):
    return VehicleState(t_ns, vid, speed, accel, 0.0, 0.0, angle, "l", "e", "r")


# --- configuration -------------------------------------------------------------------------
def test_config_parses():
    assert CFG.scenario_id == "S0" and CFG.seed == 42 and CFG.step_ns == 10_000_000
    assert [a.vehicle_id for a in CFG.fleet] == ["vehicle_01", "vehicle_02", "vehicle_03"]


def test_channel_mapping_scales_to_ten_vehicles():
    m = assign_channels([{"id": f"vehicle_{i:02d}", "profile": "model_A"} for i in range(1, 11)], "vcan{index}")
    assert [(a.vehicle_id, a.channel) for a in m][0] == ("vehicle_01", "vcan0")
    assert m[-1].vehicle_id == "vehicle_10" and m[-1].channel == "vcan9"


def test_channel_mapping_rejects_duplicates():
    with pytest.raises(ConfigError):
        assign_channels([{"id": "a"}, {"id": "a"}], "vcan{index}")
    with pytest.raises(ConfigError):
        assign_channels([{"id": "a", "channel": "vcan0"}, {"id": "b", "channel": "vcan0"}], "vcan{index}")


# --- DBC -----------------------------------------------------------------------------------
def test_dbc_is_classical_and_roundtrips():
    db = load_dbc(PROFILE.dbc)
    assert {hex(m.frame_id) for m in db.messages} == {"0x100", "0x120", "0x150", "0x180", "0x200"}
    for m in db.messages:
        assert not m.is_extended_frame and m.length == 8
    msg = db.get_message_by_name("VEHICLE_SPEED")
    data = msg.encode({"VehicleSpeed": 48.27, "LongAccel": -1.234, "Counter": 7})
    d = msg.decode(data)
    assert d["VehicleSpeed"] == pytest.approx(48.27, abs=0.01)
    assert d["LongAccel"] == pytest.approx(-1.234, abs=0.001) and d["Counter"] == 7


def test_unused_bits_are_zero():
    g = VehicleCanGenerator("vehicle_01", "vcan0", PROFILE)
    frames = g.frames_for_step(state(10_000_000, speed=12.0), 10_000_000)
    eng = next(f for f in frames if f.can_id == 0x100)
    assert eng.data[3:7] == bytes(4) and eng.data[2] >> 4 == 0 and eng.data[7] >> 4 == 0


def test_vehicle_speed_byte_layout():
    # 48.27 km/h / 0.01 = 4827 = 0x12DB -> little endian bytes DB 12 at bytes 0..1
    data = load_dbc(PROFILE.dbc).get_message_by_name("VEHICLE_SPEED").encode(
        {"VehicleSpeed": 48.27, "LongAccel": 0, "Counter": 0})
    assert data[0:2] == bytes([0xDB, 0x12])


# --- synthetic model ---------------------------------------------------------------------------
def test_gear_and_rpm_model():
    m = SyntheticVehicleModel(PROFILE.powertrain, PROFILE.chassis)
    q0 = m.quantities(state(0, speed=0.0))
    assert q0["gear"] == 1 and q0["engine_rpm"] == PROFILE.powertrain["idle_rpm"] and q0["pedal_pct"] == 0
    q = m.quantities(state(10_000_000, speed=12.0, accel=0.0))      # 43.2 km/h -> 3rd gear
    assert q["gear"] == 3 and q["engine_rpm"] == pytest.approx(12.0 / 0.31 * 60 / (2 * 3.141592653589793) * 1.4 * 3.9)
    assert m.quantities(state(15_000_000, speed=13.89))["gear"] == 4   # 50.0 km/h = upshift threshold
    assert m.quantities(state(20_000_000, speed=10, accel=-2.0))["brake_active"] == 1


# --- generator timing --------------------------------------------------------------------------
def test_generator_schedule_follows_periods_and_offsets():
    g = VehicleCanGenerator("vehicle_01", "vcan0", PROFILE)
    step = 10_000_000
    frames = []
    for k in range(1, 101):                     # 1 s of 10 ms steps, ignition at t=10 ms
        frames += g.frames_for_step(state(k * step), step)
    by_id = {}
    for f in frames:
        by_id.setdefault(f.can_id, []).append(f.sim_time_ns)
    assert len(by_id[0x100]) == 100 and len(by_id[0x120]) == 50
    assert by_id[0x120][0] == step + 1_000_000                       # ignition + 1 ms offset
    assert all(b - a == 20_000_000 for a, b in zip(by_id[0x120], by_id[0x120][1:]))
    assert all(f.dlc == 8 == len(f.data) for f in frames)
    times = [f.sim_time_ns for f in frames]
    assert times == sorted(times)


def test_fmt_time_keeps_ns_precision():
    assert fmt_time(102_500_123_456) == "102.500123456"
    assert fmt_time(10_000_000) == "0.010000000"


# --- recorder association -----------------------------------------------------------------------
def _tx(t, cid, data, ch="vcan0", vid="vehicle_01"):
    from src.can_generator import TxFrame
    return TxRecord(TxFrame(t, vid, ch, cid, len(data), data, "M"), 0)


def _cap(cid, data, seq, ch="vcan0", ts=0):
    return CapturedFrame(ch, ts, cid, False, len(data), data, seq)


def test_associate_matches_in_order_and_flags_problems():
    tx = {"vcan0": [_tx(1, 0x100, b"\x01"), _tx(2, 0x120, b"\x02"), _tx(3, 0x100, b"\x03")]}
    ok = {"vcan0": [_cap(0x100, b"\x01", 0), _cap(0x120, b"\x02", 1), _cap(0x100, b"\x03", 2)]}
    pairs, unexpected, st = associate(tx, ok, {})
    assert len(pairs) == 3 and not unexpected and st.total["matched"] == 3

    bad = {"vcan0": [_cap(0x100, b"\x01", 0), _cap(0x120, b"\xFF", 1)]}       # wrong payload + missing
    pairs, unexpected, st = associate(tx, bad, {})
    assert st.total["content_mismatch"] == 1 and st.total["missing"] == 1 and len(unexpected) == 1


def test_rows_use_captured_content_and_sumo_time():
    pairs, _, _ = associate({"vcan0": [_tx(10_000_000, 0x120, b"\xAB")]},
                            {"vcan0": [_cap(0x120, b"\xAB", 0, ts=1_000_010_500_000)]}, {})
    rows, labels = build_frame_rows(pairs, "S", "S0", anchor_wall_ns=1_000_000_000_000)
    r = rows[0]
    assert r["timestamp"] == "0.010000000" and r["payload"] == "AB" and r["can_id"] == "0x120"
    assert r["capture_offset_us"] == "500.0"
    assert frame_label_rows(labels)[0]["frame_label"] == "benign"


def test_ground_truth_rejects_unknown_sources():
    with pytest.raises(ValueError):
        frame_label_rows([{"frame_uid": "x", "session_id": "s", "frame_source": "attack_engine", "message_name": "M"}])
