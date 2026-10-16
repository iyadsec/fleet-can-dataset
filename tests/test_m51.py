"""Milestone 5.1: N<3 profile coverage, ECU release jitter, fast vs real-time saturation equivalence."""

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src import storage
from src.attack_engine import build_attacks
from src.can_generator import VehicleCanGenerator
from src.config import PROJECT_ROOT, ConfigError, VehicleAssignment, _load_profiles, build_fleet
from src.sumo_controller import VehicleState

PROFILES = _load_profiles(yaml.safe_load(open(PROJECT_ROOT / "config/profiles/profiles_abc.yaml"))["profiles"], PROJECT_ROOT)
ABC = ["profile_A", "profile_B", "profile_C"]
BAL = {"strategy": "balanced_shuffle", "profiles": ABC}
HAVE_VCAN = all(Path(f"/sys/class/net/vcan{i}").exists() for i in range(3))
STEP = 10_000_000


# --- profile assignment --------------------------------------------------------------------------------
def test_two_vehicle_fleets_reach_every_profile_pair_reproducibly():
    pairs, orders = Counter(), set()
    for seed in range(300):
        f1, _ = build_fleet({"count": 2, "profile_assignment": BAL}, PROFILES, seed)
        f2, _ = build_fleet({"count": 2, "profile_assignment": BAL}, PROFILES, seed)
        p = tuple(a.profile for a in f1)
        assert p == tuple(a.profile for a in f2) and p[0] != p[1]          # reproducible, no duplicate profile
        pairs[frozenset(p)] += 1
        orders.add(p)
    assert set(pairs) == {frozenset(x) for x in [("profile_A", "profile_B"), ("profile_A", "profile_C"), ("profile_B", "profile_C")]}
    assert min(pairs.values()) > 60 and len(orders) == 6                    # all pairs and both orders reachable


def test_fleets_of_three_or_more_are_unchanged_and_balanced():
    # identical to Milestone 5 (pre-fix) assignments measured for seeds 42 / 43
    assert "".join(a.profile[-1] for a in build_fleet({"count": 5, "profile_assignment": BAL}, PROFILES, 42)[0]) == "AABBC"
    assert "".join(a.profile[-1] for a in build_fleet({"count": 10, "profile_assignment": BAL}, PROFILES, 43)[0]) == "AABABACBCC"
    for n in (3, 4, 5, 10):
        c = Counter(a.profile for a in build_fleet({"count": n, "profile_assignment": BAL}, PROFILES, 7)[0])
        assert max(c.values()) - min(c.values()) <= 1 and len(c) == 3


# --- ECU jitter ------------------------------------------------------------------------------------------
def frames(profile="profile_A", frac=0.0, seed=0, seconds=3.0, switch_on=0, vid="v"):
    g = VehicleCanGenerator(vid, "vcan0", PROFILES[profile], switch_on, frac, seed)
    out = []
    for k in range(1, int(seconds * 100) + 1):
        out += g.frames_for_step(VehicleState(k * STEP, vid, 12.0, 0.2, 0, 0, 90.0, "l", "e", "r"), STEP)
    return out


def test_jitter_disabled_reproduces_deterministic_schedule():
    a = frames()
    b = frames(frac=0.0, seed=12345)                       # seed is irrelevant when disabled
    assert [(f.sim_time_ns, f.can_id, f.data) for f in a] == [(f.sim_time_ns, f.can_id, f.data) for f in b]
    for m in PROFILES["profile_A"].messages:
        t = [f.sim_time_ns for f in a if f.message == m.name]
        assert t[0] == STEP + round(m.offset_ms * 1e6) and set(np.diff(t)) == {round(m.period_ms * 1e6)}


def test_jitter_is_reproducible_and_seed_dependent():
    a, b, c = frames(frac=0.02, seed=1), frames(frac=0.02, seed=1), frames(frac=0.02, seed=2)
    ta, tb, tc = ([f.sim_time_ns for f in x] for x in (a, b, c))
    assert ta == tb and ta != tc


@pytest.mark.parametrize("profile", ABC)
@pytest.mark.parametrize("frac", [0.01, 0.02, 0.05])
def test_jitter_bounded_monotonic_and_preserves_nominal_period(profile, frac):
    fr = frames(profile, frac, seed=9, seconds=20.0, switch_on=333_000_000)
    for m in PROFILES[profile].messages:
        P = round(m.period_ms * 1e6)
        t = np.array([f.sim_time_ns for f in fr if f.message == m.name])
        nominal = STEP + 333_000_000 + round(m.offset_ms * 1e6) + P * np.arange(len(t))
        dev = t - nominal
        assert np.all(np.abs(dev) <= round(frac * P)) and np.all(np.diff(t) > 0)
        assert np.abs(dev).max() > 0                                          # jitter is actually applied
        assert abs((t[-1] - t[0]) / (len(t) - 1) - P) <= 2 * frac * P / (len(t) - 1) + 1   # no drift
        assert abs(dev.mean()) < 0.2 * frac * P                               # unbiased around the nominal grid
    assert [f.sim_time_ns for f in fr] == sorted(f.sim_time_ns for f in fr)


def test_jitter_streams_are_independent_per_message_and_vehicle():
    a = frames(frac=0.05, seed=4, vid="vehicle_01")
    b = frames(frac=0.05, seed=4, vid="vehicle_02")
    da = [f.sim_time_ns for f in a if f.message == "ENGINE_STATUS"]
    db = [f.sim_time_ns for f in b if f.message == "ENGINE_STATUS"]
    assert da != db                                                           # different vehicle -> different stream


def test_jitter_independent_of_attacks_and_campaigns(tmp_path):
    """Benign generation (incl. jitter) is identical whether or not attacks are configured."""
    fleet = [VehicleAssignment("vehicle_01", "profile_B", "vcan0", 0, 120_000_000)]
    by_v = {"vehicle_01": PROFILES["profile_B"]}

    def run(attacks):
        eng = build_attacks(attacks, fleet, by_v, 42)
        g = VehicleCanGenerator("vehicle_01", "vcan0", PROFILES["profile_B"], 120_000_000, 0.05, 99)
        out = []
        for k in range(1, 400):
            t0 = k * STEP
            out += [f for f in eng.process(g.frames_for_step(VehicleState(t0, "vehicle_01", 12.0, 0, 0, 0, 90.0, "l", "e", "r"), STEP),
                                           t0, t0 + STEP, {"vehicle_01"}) if not f.attack_id]
        return [(f.sim_time_ns, f.can_id, f.data) for f in out]
    flood = [{"attack_id": "F", "type": "flooding", "vehicle": "vehicle_01", "start_s": 1.0, "duration_s": 1.0,
              "params": {"can_id": 0, "rate_hz": 2000}}]
    assert run([]) == run(flood)
    # and the configured jitter seed does not depend on the campaign file
    from src.config import load_experiment
    base = load_experiment("config/m51/test/experiment_saturation.yaml")
    other = tmp_path / "e.yaml"
    other.write_text((PROJECT_ROOT / "config/m51/test/experiment_saturation.yaml").read_text()
                     .replace("attacks_config: config/m51/test/attacks_saturation.yaml", "attacks_config: config/attacks.yaml"))
    assert base.fleet_info["ecu_jitter"] == load_experiment(other).fleet_info["ecu_jitter"]


def test_jitter_fraction_bounds():
    with pytest.raises(ConfigError):
        build_fleet({"count": 2, "ecu_jitter": {"fraction": 0.3}}, PROFILES, 1)
    _, info = build_fleet({"count": 2, "ecu_jitter": {"fraction": 0.02}}, PROFILES, 1)
    assert info["ecu_jitter"]["model"] == "uniform_release" and info["ecu_jitter"]["seed"] is not None
    _, info0 = build_fleet({"count": 2}, PROFILES, 1)
    assert info0["ecu_jitter"] == {"model": "none", "fraction": 0.0, "seed": None, "per_stream_seed": None}


# --- live: real-time vs fast under saturation (queueing, queue drops, expiry) --------------------------
@pytest.mark.skipif(not HAVE_VCAN, reason="vcan0..vcan2 not up")
def test_saturation_fast_vs_realtime_logically_identical(tmp_path):
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from compare_sessions import compare
    from analysis.run_session import analyse
    from src.config import load_experiment
    from src.orchestrator import run
    sids = {}
    for name, realtime in (("rt", True), ("fast", False)):
        cfg = load_experiment("config/m51/test/experiment_saturation.yaml")
        cfg.output_root, cfg.realtime, cfg.output_format = tmp_path, realtime, "parquet"
        sid, overall = run(cfg, session_id=f"M51_SAT_{name.upper()}")
        assert overall == "PASS" and analyse(sid, tmp_path)["overall"] == "PASS"
        sids[name] = sid
    res = compare(tmp_path, sids["rt"], sids["fast"])
    assert all(v.startswith("IDENTICAL") for v in res.values()), res
    d = storage.read_table(tmp_path / "ground_truth" / sids["fast"] / "dropped_frames")
    assert (d.drop_kind == "queue_drop").sum() > 0 and (d.drop_kind == "expired_drop").sum() > 0
    for s in sids.values():
        m = json.loads((tmp_path / "metadata" / s / "metadata.json").read_text())
        assert m["versions"]["generator_version"] == "1.1.0" and m["fleet"]["assignment"]["ecu_jitter"]["fraction"] == 0.02
