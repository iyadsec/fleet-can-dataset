"""Milestone 4 analysis layer: windowing, 24 FLEET-GUARD features, timestamps, anti-leakage,
descriptor/label separation, temporal alignment (in memory, no SUMO / vCAN)."""

import numpy as np
import pandas as pd
import pytest

from analysis.descriptor_extractor import (LeakageError, assert_no_ground_truth, build_descriptors,
                                           payload_bytes, window_features, window_frame_uids)
from analysis.descriptor_schema import (DESCRIPTOR_COLUMNS, FEATURE_COLUMNS, FEATURE_TABLE, OBSERVATION_COLUMNS,
                                        PROHIBITED_FIELDS, WINDOW_SIZE, WINDOW_STRIDE)
from analysis.temporal_alignment import delta_t, pairwise_delta_t
from analysis.windowing import window_starts


def obs(n, vehicle="vehicle_01", session="S", period_ns=1_000_000, start_ns=0, ids=(0x100, 0x120), payload="0011223344556677"):
    return pd.DataFrame({
        "frame_uid": [f"{session}-{vehicle}-{i}" for i in range(n)], "session_id": session, "vehicle_id": vehicle,
        "can_id": [f"0x{ids[i % len(ids)]:03X}" for i in range(n)], "dlc": 8, "payload": payload,
        "tx_end_ns": [start_ns + i * period_ns for i in range(n)],
        "capture_time_ns": [start_ns + i * period_ns + 50_000 for i in range(n)]})


# --- schema --------------------------------------------------------------------------------------------
def test_descriptor_is_24_dimensional_and_matches_original_order():
    assert len(FEATURE_COLUMNS) == 24 == len(FEATURE_TABLE)
    assert FEATURE_COLUMNS[:8] == ["frame_count", "unique_can_id_count", "can_id_entropy", "most_common_can_id_ratio",
                                   "mean_inter_arrival_time", "std_inter_arrival_time", "mean_dlc", "std_dlc"]
    assert [r[1] for r in FEATURE_TABLE] == FEATURE_COLUMNS
    assert (WINDOW_SIZE, WINDOW_STRIDE) == (100, 50)


# --- windowing ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n,expected", [(99, []), (100, [0]), (149, [0]), (150, [0, 50]), (250, [0, 50, 100, 150])])
def test_window_starts_follow_original_rule(n, expected):
    assert list(window_starts(n)) == expected        # range(0, n - 100 + 1, 50), trailing partial dropped


def test_windows_never_cross_vehicles_or_sessions():
    o = pd.concat([obs(230, "vehicle_01", "A"), obs(180, "vehicle_02", "A"), obs(120, "vehicle_01", "B")])
    d = build_descriptors(o)
    members = window_frame_uids(o)
    for r in d.itertuples():
        uids = members[r.descriptor_id]
        assert len(uids) == 100 and all(u.startswith(f"{r.session_id}-{r.vehicle_id}-") for u in uids)
    assert d.groupby(["session_id", "vehicle_id"]).size().to_dict() == {("A", "vehicle_01"): 3, ("A", "vehicle_02"): 2,
                                                                         ("B", "vehicle_01"): 1}


def test_window_boundaries_and_timestamp_is_window_end():
    d = build_descriptors(obs(250, period_ns=2_000_000, start_ns=10_000_000))
    assert list(d.window_start_ns) == [10_000_000 + 2_000_000 * s for s in (0, 50, 100, 150)]
    assert list(d.window_end_ns) == [10_000_000 + 2_000_000 * (s + 99) for s in (0, 50, 100, 150)]
    assert (d.descriptor_timestamp_ns == d.window_end_ns).all() and d.descriptor_timestamp_ns.is_monotonic_increasing
    assert d.descriptor_timestamp.iloc[0] == f"{(10_000_000 + 2_000_000 * 99) / 1e9:.9f}"


def test_capture_time_basis_uses_capture_clock():
    o = obs(100)
    d = build_descriptors(o, time_basis="capture")
    assert d.window_end_ns.iloc[0] == o.capture_time_ns.iloc[-1] and d.time_basis.iloc[0] == "capture"


# --- feature calculations ----------------------------------------------------------------------------------
def test_entropy_and_id_features():
    ids = np.array(["A"] * 50 + ["B"] * 25 + ["C"] * 25)
    f = window_features(np.arange(100) * 0.001, ids, np.full(100, 8), np.zeros((100, 8)))
    assert f["can_id_entropy"] == pytest.approx(1.5, abs=1e-9)              # -(.5log.5 + 2*.25log.25)
    assert f["unique_can_id_count"] == 3 and f["most_common_can_id_ratio"] == 0.5 and f["frame_count"] == 100
    one = window_features(np.arange(100) * 0.001, np.array(["A"] * 100), np.full(100, 8), np.zeros((100, 8)))
    assert one["can_id_entropy"] == pytest.approx(0.0, abs=1e-9)


def test_inter_arrival_mean_and_population_std():
    t = np.cumsum(np.r_[0, np.tile([0.001, 0.003], 50)[:99]])
    f = window_features(t, np.array(["A"] * 100), np.full(100, 8), np.zeros((100, 8)))
    gaps = np.diff(t)
    assert f["mean_inter_arrival_time"] == pytest.approx(gaps.mean())
    assert f["std_inter_arrival_time"] == pytest.approx(gaps.std(ddof=0))


def test_payload_bytes_beyond_dlc_are_ignored():
    data = payload_bytes(pd.Series(["FF00", "", "0102030405060708"]), np.array([2, 0, 8]))
    assert data[0, :2].tolist() == [255, 0] and np.isnan(data[0, 2:]).all() and np.isnan(data[1]).all()
    f = window_features(np.arange(3) * 0.001, np.array(["A", "B", "C"]), np.array([2, 0, 8]), data)
    assert f["byte_mean_0"] == pytest.approx((255 + 1) / 2) and f["byte_mean_7"] == 8 and f["byte_std_7"] == 0
    assert f["mean_dlc"] == pytest.approx(10 / 3) and f["std_dlc"] == pytest.approx(np.std([2, 0, 8]))


def test_no_nan_or_inf_in_descriptors_even_when_bytes_are_absent():
    o = obs(100, payload="")
    o["dlc"] = 0
    d = build_descriptors(o)
    assert np.isfinite(d[FEATURE_COLUMNS].to_numpy(float)).all()
    assert d.nan_filled_features.iloc[0] == 16 and (d[[f"byte_mean_{i}" for i in range(8)]] == 0).all().all()


# --- anti-leakage --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field", sorted(PROHIBITED_FIELDS))
def test_prohibited_ground_truth_field_is_rejected(field):
    o = obs(100)
    o[field] = "x"
    with pytest.raises(LeakageError):
        build_descriptors(o)


def test_observation_allow_list_contains_no_prohibited_field():
    assert not set(OBSERVATION_COLUMNS) & PROHIBITED_FIELDS
    assert_no_ground_truth(obs(5))


def test_descriptors_do_not_depend_on_labels():
    """Same observations, different hidden labels elsewhere -> identical descriptors."""
    o = obs(300)
    a, b = build_descriptors(o.copy()), build_descriptors(o.copy())
    pd.testing.assert_frame_equal(a, b)
    assert list(a.columns) == DESCRIPTOR_COLUMNS and not set(a.columns) & PROHIBITED_FIELDS


# --- temporal alignment ----------------------------------------------------------------------------------------
def test_delta_t_and_pairwise_cross_vehicle_only():
    assert delta_t(10_000_000_000, 11_500_000_000) == pytest.approx(1.5)
    assert delta_t([1, 5], [4, 1]).tolist() == pytest.approx([3e-9, 4e-9])
    o = pd.concat([obs(150, "vehicle_01"), obs(150, "vehicle_02", start_ns=1_000_000_000)])
    p = pairwise_delta_t(build_descriptors(o))
    assert len(p) == 4 and (p.vehicle_id_i != p.vehicle_id_j).all()
    assert p.delta_t_s.min() == pytest.approx(0.95, abs=1e-9)      # 1 s offset minus 50-frame shift at 1 ms


# --- vehicle heterogeneity example profiles (Milestone 4 audit) -------------------------------------------
def test_heterogeneous_example_profiles_generate_decodable_frames():
    import yaml
    from src.can_generator import VehicleCanGenerator, load_dbc
    from src.config import ExperimentConfig, _load_profiles, PROJECT_ROOT
    from src.sumo_controller import VehicleState
    raw = yaml.safe_load(open(PROJECT_ROOT / "config/profiles/profiles_abc.yaml"))
    profiles = _load_profiles(raw["profiles"], PROJECT_ROOT)
    assert set(profiles) == {"profile_A", "profile_B", "profile_C"}
    summary = {}
    for name, prof in profiles.items():
        g = VehicleCanGenerator("v", "vcan0", prof)
        frames = []
        for k in range(1, 101):                                 # 1 s at 10 ms steps
            frames += g.frames_for_step(VehicleState(k * 10_000_000, "v", 12.0, 0.5, 0, 0, 90.0, "l", "e", "r"), 10_000_000)
        db = load_dbc(prof.dbc)
        speed = [db.decode_message(f.can_id, f.data) for f in frames]
        assert all(len(f.data) == db.get_message_by_frame_id(f.can_id).length for f in frames)
        summary[name] = (len({f.can_id for f in frames}), len(frames), sorted({f.dlc for f in frames}))
        assert speed
    for name, prof in profiles.items():                         # 1 s of traffic = sum of 1000 / period_ms
        assert summary[name][1] == sum(round(1000 / m.period_ms) for m in prof.messages)
    assert summary["profile_A"] == (5, 350, [8])
    assert summary["profile_B"] == (4, 270, [8])
    assert summary["profile_C"] == (5, 350, [4, 5, 6, 8])
    d = build_descriptors(pd.DataFrame({                       # descriptors stay 24-D for every profile
        "frame_uid": [str(i) for i in range(200)], "session_id": "S", "vehicle_id": "v",
        "can_id": "0x1A0", "dlc": 6, "payload": "000102030405", "tx_end_ns": range(0, 200_000_000, 1_000_000),
        "capture_time_ns": range(200)}))
    assert len([c for c in d.columns if c in FEATURE_COLUMNS]) == 24
