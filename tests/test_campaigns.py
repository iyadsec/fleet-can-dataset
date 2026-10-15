"""Campaign scheduler, campaign ground truth and timing-layer separation (in memory: no SUMO, no vCAN)."""

import pytest

from src.attack_engine import build_attacks
from src.bus_model import BusConfig, CanBusModel
from src.campaign_scheduler import (CampaignConfigError, attack_configs, build_schedule, check_scenario,
                                    pair_overlaps)
from src.can_generator import VehicleCanGenerator
from src.config import load_experiment
from src.ground_truth import attack_rows, campaign_rows, dropped_rows, frame_label_rows
from src.recorder import TxRecord
from src.sumo_controller import VehicleState

S = 1_000_000_000
V3 = ["vehicle_01", "vehicle_02", "vehicle_03"]
CFG = load_experiment("config/experiment.yaml")
BY_VEHICLE = {a.vehicle_id: CFG.profiles[a.profile] for a in CFG.fleet}


def camp(cid, typ="replay", coordinated=True, targets=V3, start=10.0, pattern="simultaneous", **kw):
    c = {"campaign_id": cid, "attack_type": typ, "coordinated": coordinated, "targets": list(targets),
         "start_s": start, "delay_pattern": pattern}
    if typ == "replay":
        c["params"] = {"window_relative_s": [-5.0, 0.0], "source_vehicle": "self"}
    else:
        c["duration_s"] = 5.0
        c["params"] = {"flooding": {"can_id": 0, "rate_hz": 500}, "fuzzy": {"mode": "both", "rate_hz": 500},
                       "malfunction": {"mode": "inject", "message": "VEHICLE_SPEED", "signal": "VehicleSpeed",
                                       "manipulation": {"strategy": "constant", "value": 250.0}}}.get(typ, {})
    c.update(kw)
    return c


def starts(c):
    return {m.vehicle_id: m.start_ns for m in c.members}


# --- parsing --------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    [camp("C1"), camp("C1")],                                            # duplicate id
    [camp("C1", targets=["vehicle_01"])],                                 # coordinated with one vehicle
    [camp("C1", coordinated=False)],                                      # independent with three vehicles
    [camp("C1", targets=["vehicle_01", "vehicle_09"])],                   # unknown vehicle
    [camp("C1", pattern="fixed", relative_delays_s=[0, 1])],              # one delay missing
    [camp("C1", pattern="fixed", relative_delays_s=[0, -1, 2])],          # negative delay
    [camp("C1", pattern="wave")],                                         # unknown pattern
    [camp("C1", typ="teleport")],                                         # unknown attack type
    [{**camp("C1"), "params": {}}],                                       # replay without window
    [{**camp("C1"), "coordinated": "yes"}],                               # coordinated must be boolean
])
def test_invalid_campaigns_are_rejected(bad):
    with pytest.raises(CampaignConfigError):
        build_schedule(bad, 42, V3)


# --- timing patterns --------------------------------------------------------------------------------
def test_simultaneous_campaign_has_identical_starts_and_zero_delays():
    (c,) = build_schedule([camp("C001")], 42, V3)
    assert set(starts(c).values()) == {10 * S} and all(m.relative_delay_ns == 0 for m in c.members)
    assert c.inter_vehicle_delays_ns() == [0, 0, 0]


@pytest.mark.parametrize("delays,expected", [([0, 1, 2], [0, 1, 2]), ([0, 5, 10], [0, 5, 10]), ([0, 0.001, 0.0035], [0, 0.001, 0.0035])])
def test_fixed_stagger_preserves_delays_exactly(delays, expected):
    (c,) = build_schedule([camp("C002", pattern="fixed", relative_delays_s=delays)], 42, V3)
    assert [m.relative_delay_ns for m in c.members] == [round(x * S) for x in expected]
    assert [m.start_ns for m in c.members] == [10 * S + round(x * S) for x in expected]


def test_partial_overlap_metrics_follow_definitions():
    (c,) = build_schedule([camp("C004", typ="flooding", pattern="fixed", relative_delays_s=[0, 5, 8],
                                durations_s=[10, 10, 10])], 42, V3)
    assert [(m.start_ns, m.end_ns) for m in c.members] == [(10 * S, 20 * S), (15 * S, 25 * S), (18 * S, 28 * S)]
    assert c.common_overlap_ns() == 2 * S and c.end_ns == 28 * S
    ov = {(o["vehicle_a"], o["vehicle_b"]): o for o in pair_overlaps(c)}
    assert ov[("vehicle_01", "vehicle_02")]["overlap_ns"] == 5 * S
    assert ov[("vehicle_01", "vehicle_02")]["jaccard"] == pytest.approx(5 / 15)
    assert ov[("vehicle_01", "vehicle_03")]["overlap_ratio_min"] == pytest.approx(0.2)
    assert ov[("vehicle_02", "vehicle_03")]["overlap_ns"] == 7 * S


def test_uniform_random_delays_are_seeded_bounded_and_quantised():
    c1 = build_schedule([camp("C010", pattern="uniform_random", delay_range_s=[0, 5], delay_resolution_s=0.001)], 42, V3)[0]
    c2 = build_schedule([camp("C010", pattern="uniform_random", delay_range_s=[0, 5], delay_resolution_s=0.001)], 42, V3)[0]
    c3 = build_schedule([camp("C010", pattern="uniform_random", delay_range_s=[0, 5], delay_resolution_s=0.001)], 7, V3)[0]
    d = [m.relative_delay_ns for m in c1.members]
    assert d == [m.relative_delay_ns for m in c2.members] != [m.relative_delay_ns for m in c3.members]
    assert all(0 <= x <= 5 * S and x % 1_000_000 == 0 for x in d)


def test_replay_window_is_relative_to_each_vehicle_start():
    (c,) = build_schedule([camp("C003", pattern="fixed", relative_delays_s=[0, 5, 10])], 42, V3)
    for m in c.members:
        assert m.params["window_ns"] == [m.start_ns - 5 * S, m.start_ns]
        assert m.params["source_vehicle"] == m.vehicle_id and m.end_ns == m.start_ns + 5 * S


# --- independent / mixed membership -------------------------------------------------------------------
def test_independent_attacks_get_separate_campaigns_even_when_similar_and_close():
    cs = build_schedule([camp(f"C10{i}", coordinated=False, targets=[v], start=10.0 + 0.2 * i)
                         for i, v in enumerate(V3, 1)], 42, V3)
    acfg = attack_configs(cs)
    assert [a["campaign_id"] for a in acfg] == ["C101", "C102", "C103"]
    assert len({a["attack_id"] for a in acfg}) == 3 and all(not c.coordinated for c in cs)
    assert check_scenario("S2", [], cs) == [] and check_scenario("S3", [], cs) != []


def test_mixed_scenario_preserves_partial_membership():
    cs = build_schedule([camp("C201", targets=["vehicle_01", "vehicle_02"], pattern="fixed", relative_delays_s=[0, 0.5]),
                         camp("C202", coordinated=False, targets=["vehicle_03"], start=10.3)], 42, V3)
    member_of = {a["vehicle"]: a["campaign_id"] for a in attack_configs(cs)}
    assert member_of == {"vehicle_01": "C201", "vehicle_02": "C201", "vehicle_03": "C202"}
    assert check_scenario("S3", [], cs) == []


def test_scenario_definitions():
    coord = build_schedule([camp("C1")], 42, V3)
    assert check_scenario("S0", [], []) == [] and check_scenario("S0", [], coord)
    assert check_scenario("S1", [{"attack_id": "A"}], []) == [] and check_scenario("S1", [], coord)
    assert check_scenario("S4", [], coord)                       # S4 is not generated


# --- end to end in memory: frame -> attack -> campaign, timing layers ----------------------------------
def simulate_fleet(campaigns, seconds, bus=BusConfig(), extra_attacks=()):
    cs = build_schedule(campaigns, 42, V3)
    eng = build_attacks(list(extra_attacks) + attack_configs(cs), CFG.fleet, BY_VEHICLE, 42,
                        campaign_ids={c.campaign_id for c in cs})
    gens = {a.vehicle_id: VehicleCanGenerator(a.vehicle_id, a.channel, CFG.profiles[a.profile]) for a in CFG.fleet}
    buses = {a.channel: CanBusModel(bus, a.channel) for a in CFG.fleet}
    txs, drops = [], []
    for k in range(1, int(seconds * 100) + 1):
        t0 = k * 10_000_000
        frames = []
        for v, g in gens.items():
            frames += g.frames_for_step(VehicleState(t0, v, 12.0, 0.0, 0, 0, 90.0, "l", "e", "r"), 10_000_000)
        for fr in eng.process(frames, t0, t0 + 10_000_000, set(gens)):
            buses[fr.channel].submit(fr, fr.node)
        for b in buses.values():
            t, d = b.advance_until(t0 + 10_000_000)
            eng.observe(t)
            txs += t
            drops += d
    for b in buses.values():
        t, d = b.drain()
        eng.observe(t)
        txs += t
        drops += d
    return cs, eng, txs, drops


def test_frames_trace_to_attack_and_campaign_and_benign_has_none():
    cs, eng, txs, drops = simulate_fleet([camp("C201", typ="flooding", targets=["vehicle_01", "vehicle_02"], start=1.0,
                                               duration_s=0.5),
                                          camp("C202", typ="flooding", coordinated=False, targets=["vehicle_03"],
                                               start=1.1, duration_s=0.5)], 2.0)
    labels = frame_label_rows([{"frame_uid": str(i), "frame": t.frame} for i, t in enumerate(txs)], "s")
    by_attack = {}
    for l in labels:
        if l["frame_label"] == "benign":
            assert l["campaign_id"] == "None" and l["attack_id"] == "None"
        else:
            by_attack.setdefault(l["attack_id"], set()).add(l["campaign_id"])
    assert by_attack == {"C201_vehicle_01": {"C201"}, "C201_vehicle_02": {"C201"}, "C202_vehicle_03": {"C202"}}
    rows = {r["attack_id"]: r for r in attack_rows(eng, [(TxRecord(t, 0), None) for t in txs], drops, "S3", "s")}
    assert rows["C201_vehicle_02"]["campaign_id"] == "C201" and rows["C202_vehicle_03"]["campaign_id"] == "C202"


def test_campaign_timing_ignores_queueing_delay():
    """vehicle_02's bus is flooded (isolated attack) so its replayed frames queue, but the campaign's
    logical starts and relative delays are unchanged and the first generation times stay exact."""
    flood = {"attack_id": "NOISE", "type": "flooding", "vehicle": "vehicle_02", "start_s": 2.0, "duration_s": 2.0,
             "params": {"can_id": 0, "rate_hz": 3500}}
    cs, eng, txs, drops = simulate_fleet([camp("C002", typ="fuzzy", start=2.5, pattern="fixed",
                                               relative_delays_s=[0, 0.25, 0.5], duration_s=0.5)], 3.5,
                                         extra_attacks=[flood])
    (c,) = cs
    first_gen, first_tx = {}, {}
    for t in txs:
        if t.frame.campaign_id == "C002":
            first_gen[t.frame.vehicle_id] = min(first_gen.get(t.frame.vehicle_id, 1 << 62), t.frame.sim_time_ns)
            first_tx[t.frame.vehicle_id] = min(first_tx.get(t.frame.vehicle_id, 1 << 62), t.start_ns)
    assert first_gen == {m.vehicle_id: m.start_ns for m in c.members}                # logical layer exact
    q = first_tx["vehicle_02"] - first_gen["vehicle_02"]
    assert q > 0                                                                       # vehicle_02 frames waited on the bus
    rows, members, _ = campaign_rows(cs, "S3", "s")
    assert [int(m["relative_delay_ns"]) for m in members] == [0, 250_000_000, 500_000_000]


def test_expired_campaign_frames_stay_in_ground_truth():
    cs, eng, txs, drops = simulate_fleet([camp("C005", typ="flooding", start=1.0, duration_s=0.5,
                                               params={"can_id": 0, "rate_hz": 6000})], 2.0,
                                         bus=BusConfig(expiry_policy="deadline", max_frame_age_ms=20.0))
    rows = dropped_rows(drops, "s")
    exp = [r for r in rows if r["drop_kind"] == "expired_drop"]
    assert exp and all(r["campaign_id"] in ("C005", "None") for r in rows)
    assert any(r["campaign_id"] == "C005" for r in rows) and any(r["frame_label"] == "benign" for r in exp)
    assert all(t.queue_delay_ns <= 20_000_000 for t in txs)
