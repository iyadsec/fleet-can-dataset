"""Milestone 8: generator scalability to 100 vehicles (route generation, IDs, profiles, campaigns, GT, descriptors)."""

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analysis.descriptor_extractor import build_descriptors
from src import route_generator as RG
from src.campaign_scheduler import build_schedule, check_scenario
from src.config import ConfigError, build_fleet, derive_seed, load_experiment
from src.ground_truth import campaign_rows

ROOT = Path(__file__).resolve().parent.parent
NET = ROOT / "sumo" / "network" / "grid4x4.net.xml"
DATA = ROOT / "data"
PROFILES = {p: None for p in ("profile_A", "profile_B", "profile_C")}


def fleet(n, seed=1234, pattern="vehicle_{n:03d}"):
    sec = {"count": n, "id_pattern": pattern, "channel_pattern": "vcan{index}",
           "profile_assignment": {"strategy": "balanced_shuffle", "profiles": list(PROFILES)},
           "switch_on": {"range_s": [0.0, 1.0], "resolution_s": 1e-6}, "ecu_jitter": {"fraction": 0.01}}
    return build_fleet(sec, PROFILES, seed)[0]


# ---------------------------------------------------------------- fleet size, IDs, profiles
@pytest.mark.parametrize("n,counts", [(20, (7, 7, 6)), (50, (17, 17, 16)), (100, (34, 33, 33))])
def test_profile_assignment_large_n(n, counts):
    f = fleet(n)
    got = tuple(sum(a.profile == p for a in f) for p in PROFILES)
    assert got == counts
    assert [a.profile for a in fleet(n)] == [a.profile for a in f]                  # deterministic
    assert [a.profile for a in fleet(n, seed=99)] != [a.profile for a in f]         # seed-dependent order


def test_arbitrary_fleet_size_ids_and_channels():
    f = fleet(100)
    assert [a.vehicle_id for a in f] == [f"vehicle_{i:03d}" for i in range(1, 101)]
    assert f[-1].vehicle_id == "vehicle_100" and len({a.vehicle_id for a in f}) == 100
    assert [a.channel for a in f] == [f"vcan{i}" for i in range(100)] and f[-1].channel == "vcan99"
    assert len({a.switch_on_ns for a in f}) == 100


def test_legacy_ids_unchanged():
    assert [a.vehicle_id for a in fleet(10, pattern="vehicle_{n:02d}")] == [f"vehicle_{i:02d}" for i in range(1, 11)]


def test_switch_on_prefix_stable_when_fleet_grows():
    """Adding vehicles appends draws: the first 10 vehicles keep their offsets (seed derivation unchanged)."""
    assert [a.switch_on_ns for a in fleet(100)][:10] == [a.switch_on_ns for a in fleet(10)]


# ---------------------------------------------------------------- route generation
def test_route_generation_deterministic_and_seeded():
    ids = [f"vehicle_{i:03d}" for i in range(1, 101)]
    x1, i1 = RG.generate(NET, ids, 42, 40.0)
    x2, i2 = RG.generate(NET, ids, 42, 40.0)
    x3, _ = RG.generate(NET, ids, 43, 40.0)
    assert x1 == x2 and i1 == i2 and x1 != x3


def test_route_validity_and_capacity():
    edges, conns = RG.read_network(NET)
    RG.validate_loops(edges, conns)
    cand = RG.slots(edges)
    assert len(cand) == 168
    ids = [f"vehicle_{i:03d}" for i in range(1, 101)]
    xml, info = RG.generate(NET, ids, 7, 110.0)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    vs = root.findall("vehicle")
    assert [v.get("id") for v in vs] == ids
    routes = {r.get("id"): r.get("edges").split() for r in root.findall("route")}
    starts = {}
    for v in vs:
        e = routes[v.get("route")]
        assert all(x in edges for x in e) and all((a, b) in conns for a, b in zip(e, e[1:]))
        assert v.get("depart") == "0" and v.get("departSpeed") == "0"
        length = sum(edges[x][0] for x in e)
        assert length >= 110.0 * 13.89 * 2                                     # never leaves the network early
        starts.setdefault(e[0], []).append(float(v.get("departPos")))
    for ps in starts.values():                                                  # no two vehicles closer than 30 m
        ps = sorted(ps)
        assert all(b - a >= 30 - 1e-9 for a, b in zip(ps, ps[1:]))
    assert sum(info["loop_counts"].values()) == 100
    with pytest.raises(RG.RouteGenerationError):
        RG.generate(NET, [f"v{i}" for i in range(169)], 1, 40.0)
    with pytest.raises(RG.RouteGenerationError):
        RG.generate(NET, ["a", "a"], 1, 40.0)


def test_route_seed_is_an_independent_stream():
    assert derive_seed(5, "route_generation") not in {derive_seed(5, t) for t in ("profile_assignment", "switch_on", "ecu_jitter")}


def test_config_requires_exactly_one_scenario_source(tmp_path):
    base = (ROOT / "config/m8/experiments/M8_S0_N020.yaml")
    if not base.exists():
        pytest.skip("run scripts/m8_scaling.py configs first")
    txt = base.read_text()
    ok = load_experiment(base)
    assert ok.sumo_config is None and ok.route_generation["method"] == "grid_loops_v1"
    both = tmp_path / "b.yaml"
    both.write_text(txt.replace("  route_generation:", "  sumo_config: sumo/scenarios/fleet_10.sumocfg\n  route_generation:"))
    with pytest.raises(ConfigError):
        load_experiment(both)
    legacy = load_experiment(ROOT / "config/final/matrix/N10_S0_none_none_none/experiment_r1.yaml")
    assert legacy.route_generation is None and legacy.sumo_config.name == "fleet_10.sumocfg"


# ---------------------------------------------------------------- vCAN guard
def test_vcan_guard_allows_100_and_rejects_beyond_max():
    r100 = subprocess.run(["bash", str(ROOT / "scripts/setup_vcan.sh"), "100"], capture_output=True, text=True)
    r200 = subprocess.run(["bash", str(ROOT / "scripts/setup_vcan.sh"), "200"], capture_output=True, text=True)
    if r100.returncode == 0:
        pytest.skip("running as root")
    assert "sudo" in r100.stderr and r100.returncode == 1          # passes the range guard, stops at the root check
    assert r200.returncode == 2 and "1..128" in r200.stderr


def test_no_hard_coded_ten_vehicle_assumptions():
    pat = re.compile(r"range\(10\)|vcan9|\[-2:\]|vehicle_10\b|\bN\s*<=?\s*10\b")
    hits = [f"{p.name}:{i}" for d in ("src", "analysis") for p in (ROOT / d).glob("*.py")
            for i, line in enumerate(p.read_text().splitlines(), 1) if pat.search(line) and not line.strip().startswith("#")]
    assert hits == []


# ---------------------------------------------------------------- campaigns / GT / descriptors beyond 10
def test_campaign_scheduler_and_gt_with_100_members():
    ids = [f"vehicle_{i:03d}" for i in range(1, 101)]
    camps = build_schedule([{"campaign_id": "C100", "attack_type": "fuzzy", "coordinated": True, "targets": ids, "start_s": 10.0,
                             "delay_pattern": "fixed", "relative_delays_s": [0.1 * i for i in range(100)], "duration_s": 5.0,
                             "params": {"mode": "both", "rate_hz": 1000}}], seed=3, fleet_vehicles=ids)
    assert check_scenario("S3", [], camps) == []
    c_rows, m_rows, o_rows = campaign_rows(camps, "S3", "sess")
    assert len(m_rows) == 100 and len(o_rows) == 100 * 99 // 2
    last = next(r for r in m_rows if r["vehicle_id"] == "vehicle_100")
    assert int(last["relative_delay_ns"]) == round(9.9 * 1e9)
    assert {r["vehicle_id"] for r in m_rows} == set(ids)


def test_independent_campaigns_beyond_ten_not_merged():
    ids = [f"vehicle_{i:03d}" for i in range(1, 51)]
    cfg = [{"campaign_id": f"I{j}", "attack_type": "replay", "coordinated": False, "targets": [ids[j]], "start_s": 10.0,
            "delay_pattern": "simultaneous", "params": {"window_relative_s": [-5, 0], "source_vehicle": "self"}} for j in range(12)]
    camps = build_schedule(cfg, seed=4, fleet_vehicles=ids)
    assert check_scenario("S2", [], camps) == [] and len({c.campaign_id for c in camps}) == 12


def test_descriptor_extraction_beyond_ten_vehicles():
    rng = np.random.default_rng(0)
    rows = []
    for v in ("vehicle_009", "vehicle_010", "vehicle_011", "vehicle_100"):
        for k in range(260):
            rows.append({"frame_uid": f"s-F{len(rows):09d}", "session_id": "s", "vehicle_id": v, "can_id": "0x100", "dlc": 8,
                         "payload": "00" * 8, "tx_end_ns": 1_000_000 * k + int(rng.integers(0, 1000)), "capture_time_ns": 0})
    d = build_descriptors(pd.DataFrame(rows))
    per = d.groupby("vehicle_id").size()
    assert set(per.index) == {"vehicle_009", "vehicle_010", "vehicle_011", "vehicle_100"} and set(per) == {(260 - 100) // 50 + 1}
    assert d.descriptor_id.is_unique and d[d.vehicle_id == "vehicle_100"].descriptor_id.str.contains("vehicle_100").all()


# ---------------------------------------------------------------- data-backed (skip if the M8 runs are absent)
def _has(*sids):
    return all((DATA / "metadata" / s / "metadata.json").exists() for s in sids)


@pytest.mark.skipif(not _has("M8_S0_N100"), reason="M8 N=100 run not present")
def test_n100_session_ids_uids_and_vehicle_100():
    fr = pd.read_parquet(DATA / "raw/M8_S0_N100/can_frames.parquet", columns=["frame_uid", "vehicle_id", "channel"])
    assert fr.frame_uid.is_unique and fr.vehicle_id.nunique() == 100 and fr.channel.nunique() == 100
    assert set(fr[fr.vehicle_id == "vehicle_100"].channel) == {"vcan99"}
    d = pd.read_parquet(DATA / "analysis/M8_S0_N100/descriptors.parquet", columns=["descriptor_id", "vehicle_id"])
    assert d.descriptor_id.is_unique and "vehicle_100" in set(d.vehicle_id)


@pytest.mark.skipif(not _has("M8_S0_N020", "M8_S0_N020_REPEAT"), reason="M8 repeat runs not present")
def test_same_seed_reproduction_n20():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from compare_sessions import compare
    assert all(v.startswith("IDENTICAL") for v in compare(DATA, "M8_S0_N020", "M8_S0_N020_REPEAT").values())


@pytest.mark.skipif(not _has("FC11_N10_S0_none_none_none_R1", "M8_REG_FC11_N10_S0_none_none_none_R1"), reason="regression run not present")
def test_n10_regression_equivalence_with_frozen_session():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from compare_sessions import compare
    res = compare(DATA, "FC11_N10_S0_none_none_none_R1", "M8_REG_FC11_N10_S0_none_none_none_R1")
    assert all(v.startswith("IDENTICAL") for v in res.values()), res
