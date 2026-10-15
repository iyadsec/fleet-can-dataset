"""Milestone 5: heterogeneous profiles, seeded assignment and switch-on, cross-profile attack semantics,
campaign-timing independence, CSV/Parquet storage, fast-mode logical equivalence, versions, manifest."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src import storage
from src.attack_engine import AttackConfigError, build_attacks, resolve_semantic_targets
from src.bus_model import CanBusModel
from src.campaign_scheduler import attack_configs, build_schedule
from src.can_generator import VehicleCanGenerator, load_dbc
from src.config import PROJECT_ROOT, ConfigError, VehicleAssignment, _load_profiles, build_fleet, load_experiment
from src.sumo_controller import VehicleState

PROFILES = _load_profiles(yaml.safe_load(open(PROJECT_ROOT / "config/profiles/profiles_abc.yaml"))["profiles"], PROJECT_ROOT)
ABC = ["profile_A", "profile_B", "profile_C"]
S = 1_000_000_000
HAVE_VCAN = all(Path(f"/sys/class/net/vcan{i}").exists() for i in range(3))


# --- profile assignment -------------------------------------------------------------------------------
def test_round_robin_and_explicit_assignment():
    fleet, info = build_fleet({"count": 5, "profile_assignment": {"strategy": "round_robin", "profiles": ABC}}, PROFILES, 42)
    assert [a.profile for a in fleet] == ["profile_A", "profile_B", "profile_C", "profile_A", "profile_B"]
    assert [a.vehicle_id for a in fleet] == [f"vehicle_{i:02d}" for i in range(1, 6)] and info["profile_assignment"]["strategy"] == "round_robin"
    fleet, _ = build_fleet({"vehicles": [{"id": "x", "profile": "profile_C"}]}, PROFILES, 42)
    assert fleet[0].profile == "profile_C"


@pytest.mark.parametrize("n", [2, 5, 10])
def test_balanced_shuffle_is_balanced_and_reproducible(n):
    sec = {"count": n, "profile_assignment": {"strategy": "balanced_shuffle", "profiles": ABC}}
    a, ia = build_fleet(sec, PROFILES, 42)
    b, _ = build_fleet(sec, PROFILES, 42)
    assert [x.profile for x in a] == [x.profile for x in b] and ia["profile_assignment"]["seed"] is not None
    counts = pd.Series([x.profile for x in a]).value_counts()
    assert counts.max() - counts.min() <= 1 and (n < 3 or set(counts.index) == set(ABC))
    seeds = {tuple(x.profile for x in build_fleet(sec, PROFILES, s)[0]) for s in range(20)}
    assert len(seeds) > 1                                         # the seed actually changes the assignment


# --- switch-on offsets ------------------------------------------------------------------------------------
def test_switch_on_offsets_reproducible_bounded_quantised():
    sec = {"count": 10, "switch_on": {"range_s": [0.0, 0.5], "resolution_s": 0.000001}}
    a, info = build_fleet(sec, PROFILES | {"profile_A": PROFILES["profile_A"]}, 7)
    b, _ = build_fleet(sec, PROFILES, 7)
    c, _ = build_fleet(sec, PROFILES, 8)
    offs = [x.switch_on_ns for x in a]
    assert offs == [x.switch_on_ns for x in b] != [x.switch_on_ns for x in c]
    assert all(0 <= o <= 500_000_000 and o % 1000 == 0 for o in offs) and len(set(offs)) == 10
    assert info["switch_on"]["seed"] is not None and info["switch_on"]["range_s"] == [0.0, 0.5]
    with pytest.raises(ConfigError):
        build_fleet({"count": 2, "switch_on": {"range_s": [0.5, 0.1]}}, PROFILES, 1)


def test_fleet_does_not_depend_on_attacks_or_campaigns(tmp_path):
    base = load_experiment("config/m5/test/experiment_test.yaml")
    other = tmp_path / "exp.yaml"
    other.write_text(Path(PROJECT_ROOT / "config/m5/test/experiment_test.yaml").read_text()
                     .replace("campaigns_config: config/m5/test/campaigns_test.yaml", "campaigns_config: config/campaigns.yaml"))
    alt = load_experiment(other)
    assert [(a.vehicle_id, a.profile, a.switch_on_ns) for a in base.fleet] == [(a.vehicle_id, a.profile, a.switch_on_ns) for a in alt.fleet]


def test_generator_starts_after_switch_on():
    g = VehicleCanGenerator("v", "vcan0", PROFILES["profile_B"], switch_on_ns=123_456_000)
    frames = []
    for k in range(1, 60):
        frames += g.frames_for_step(VehicleState(k * 10_000_000, "v", 10.0, 0, 0, 0, 90.0, "l", "e", "r"), 10_000_000)
    first = {}
    for f in frames:
        first.setdefault(f.message, f.sim_time_ns)
    for m in PROFILES["profile_B"].messages:
        assert first[m.name] == 10_000_000 + 123_456_000 + round(m.offset_ms * 1e6)


# --- campaign timing independent of switch-on ---------------------------------------------------------------
def test_campaign_schedule_unchanged_by_switch_on():
    camp = [{"campaign_id": "C1", "attack_type": "flooding", "coordinated": True, "targets": ["vehicle_01", "vehicle_02"],
             "start_s": 10.0, "delay_pattern": "fixed", "relative_delays_s": [0.0, 0.5], "duration_s": 2.0,
             "params": {"can_id": 0, "rate_hz": 200}}]
    (c,) = build_schedule(camp, 42, ["vehicle_01", "vehicle_02"])
    assert [(m.start_ns, m.relative_delay_ns) for m in c.members] == [(10 * S, 0), (10_500_000_000, 500_000_000)]
    # simulate with very different switch-on offsets: first attack generation is still exactly at the configured start
    fleet = [VehicleAssignment("vehicle_01", "profile_A", "vcan0", 0, 120_000_000),
             VehicleAssignment("vehicle_02", "profile_C", "vcan1", 1, 430_000_000)]
    by_v = {a.vehicle_id: PROFILES[a.profile] for a in fleet}
    eng = build_attacks(attack_configs([c]), fleet, by_v, 42, campaign_ids={"C1"})
    gens = {a.vehicle_id: VehicleCanGenerator(a.vehicle_id, a.channel, PROFILES[a.profile], a.switch_on_ns) for a in fleet}
    first = {}
    for k in range(1, 1300):
        t0 = k * 10_000_000
        fr = []
        for v, g in gens.items():
            fr += g.frames_for_step(VehicleState(t0, v, 12.0, 0, 0, 0, 90.0, "l", "e", "r"), 10_000_000)
        for f in eng.process(fr, t0, t0 + 10_000_000, set(gens)):
            if f.campaign_id:
                first.setdefault(f.vehicle_id, f.sim_time_ns)
    assert first == {"vehicle_01": 10 * S, "vehicle_02": 10_500_000_000}


# --- cross-profile attack semantics ------------------------------------------------------------------------
def run_one(profile, attack, seconds=3.0):
    fleet = [VehicleAssignment("vehicle_01", profile, "vcan0", 0, 37_000_000)]
    by_v = {"vehicle_01": PROFILES[profile]}
    eng = build_attacks(resolve_semantic_targets([attack], by_v), fleet, by_v, 42)
    gen = VehicleCanGenerator("vehicle_01", "vcan0", PROFILES[profile], 37_000_000)
    bus = CanBusModel(PROFILES[profile].bus, "vcan0")
    txs = []
    for k in range(1, int(seconds * 100) + 1):
        t0 = k * 10_000_000
        fr = eng.process(gen.frames_for_step(VehicleState(t0, "vehicle_01", 12.0, 0.3, 0, 0, 90.0, "l", "e", "r"), 10_000_000),
                         t0, t0 + 10_000_000, {"vehicle_01"})
        for f in fr:
            bus.submit(f, f.node)
        t, _ = bus.advance_until(t0 + 10_000_000)
        eng.observe(t)
        txs += t
    return [t.frame for t in txs + bus.drain()[0]]


def att(typ, **params):
    a = {"attack_id": "X", "type": typ, "vehicle": "vehicle_01", "start_s": 1.0, "params": params}
    if typ != "replay":
        a["duration_s"] = 1.0
    return a


@pytest.mark.parametrize("profile", ABC)
@pytest.mark.parametrize("mode", ["inject", "replace"])
def test_malfunction_vehicle_speed_on_every_profile(profile, mode):
    frames = run_one(profile, att("malfunction", mode=mode, target="vehicle_speed",
                                  manipulation={"strategy": "constant", "value": 250.0}, rate_hz=50))
    msg_name, sig = PROFILES[profile].roles["vehicle_speed"]
    db = load_dbc(PROFILES[profile].dbc)
    msg = db.get_message_by_name(msg_name)
    spec = next(m for m in PROFILES[profile].messages if m.name == msg_name)
    ctr = next((s for s, q in spec.signals.items() if q == "counter"), None)
    mal = [f for f in frames if f.attack_id]
    assert mal and all(f.can_id == msg.frame_id for f in mal)
    for f in mal:
        d = msg.decode(f.data)
        assert d[sig] == pytest.approx(250.0, abs=msg.get_signal_by_name(sig).scale)
        assert f.ref["expected_value"] == pytest.approx(12.0 * 3.6, abs=msg.get_signal_by_name(sig).scale)
        if mode == "replace":
            orig = msg.decode(bytes.fromhex(f.ref["original_payload"]))
            assert all(orig[k] == d[k] for k in orig if k != sig)        # only the semantic target changed
        elif ctr:
            legit = [x for x in frames if x.can_id == f.can_id and not x.attack_id and x.sim_time_ns <= f.sim_time_ns]
            assert int(d[ctr]) == (int(msg.decode(legit[-1].data)[ctr]) + 1) % 16


@pytest.mark.parametrize("profile", ABC)
def test_fuzzy_payload_targets_the_profiles_speed_message(profile):
    frames = run_one(profile, att("fuzzy", mode="payload", target="vehicle_speed", rate_hz=200, dlc=8))
    msg = load_dbc(PROFILES[profile].dbc).get_message_by_name(PROFILES[profile].roles["vehicle_speed"][0])
    assert {f.can_id for f in frames if f.attack_id} == {msg.frame_id}


@pytest.mark.parametrize("profile", ABC)
@pytest.mark.parametrize("typ,params", [("flooding", {"can_id": 0, "rate_hz": 1000}),
                                        ("fuzzy", {"mode": "both", "rate_hz": 500, "dlc": "random"}),
                                        ("replay", {"source_vehicle": "vehicle_01", "window_s": [0.4, 0.9]})])
def test_profile_independent_attacks_work_on_every_profile(profile, typ, params):
    frames = run_one(profile, att(typ, **params))
    mal = [f for f in frames if f.attack_id]
    assert mal
    if typ == "replay":
        originals = {(f.can_id, f.data) for f in frames if not f.attack_id}
        assert all((f.can_id, f.data) in originals for f in mal)
        ids = {m.name for m in PROFILES[profile].messages}
        assert {f.message for f in mal} <= ids                     # replays this profile's own messages


def test_semantic_target_must_exist():
    with pytest.raises(AttackConfigError):
        resolve_semantic_targets([att("malfunction", target="wiper_speed", manipulation={"strategy": "constant", "value": 1})],
                                 {"vehicle_01": PROFILES["profile_A"]})


# --- storage -----------------------------------------------------------------------------------------------
def test_csv_and_parquet_tables_read_back_identically(tmp_path):
    cols = ["a", "b", "c", "d"]
    rows = [{"a": 1, "b": "x", "c": "", "d": 3}, {"a": 2, "b": "", "c": "0.5000", "d": ""}]
    storage.write_table(tmp_path / "t", cols, rows, "csv")
    csv_df = storage.read_table(tmp_path / "t")
    storage.write_table(tmp_path / "t", cols, rows, "parquet")
    assert not (tmp_path / "t.csv").exists() and storage.table_format(tmp_path / "t") == "parquet"
    pd.testing.assert_frame_equal(csv_df, storage.read_table(tmp_path / "t"))
    assert storage.read_columns(tmp_path / "t") == cols


# --- live: fast-mode and storage equivalence, versions, manifest ----------------------------------------------
LOGICAL_FRAME = ["vehicle_id", "sim_time_ns", "tx_start_ns", "tx_end_ns", "queue_delay_us", "frame_bits",
                 "can_id", "dlc", "payload", "timestamp", "transmission_timestamp"]


def deterministic(data: Path, sid: str) -> dict[str, pd.DataFrame]:
    out = {"frames": storage.read_table(data / "raw" / sid / "can_frames")[LOGICAL_FRAME],
           "states": storage.read_table(data / "raw" / sid / "vehicle_states").drop(columns=["session_id"])}
    for t in ["frame_labels", "dropped_frames", "attacks", "attack_frames", "replay_map", "campaigns",
              "campaign_membership", "campaign_overlap"]:
        df = storage.read_table(data / "ground_truth" / sid / t)
        drop = [c for c in df.columns if c in ("session_id", "frame_uid", "replay_frame_uid", "original_frame_uid")]
        out[t] = df.drop(columns=drop)
    for k, df in out.items():
        out[k] = df.sort_values(list(df.columns)).reset_index(drop=True)
    return out


@pytest.mark.skipif(not HAVE_VCAN, reason="vcan0..vcan2 not up")
def test_fast_mode_and_parquet_preserve_all_logical_results(tmp_path):
    from analysis.descriptor_schema import FEATURE_COLUMNS
    from analysis.run_session import analyse
    from src.orchestrator import run
    sids = {}
    for name, realtime, fmt in [("rt_csv", True, "csv"), ("fast_csv", False, "csv"), ("fast_parquet", False, "parquet")]:
        cfg = load_experiment("config/m5/test/experiment_test.yaml")
        cfg.output_root, cfg.realtime, cfg.output_format = tmp_path, realtime, fmt
        sid, overall = run(cfg, session_id=f"M5_EQ_{name.upper()}")
        assert overall == "PASS"
        assert analyse(sid, tmp_path)["overall"] == "PASS"
        sids[name] = sid
    assert storage.table_format(tmp_path / "raw" / sids["fast_parquet"] / "can_frames") == "parquet"
    ref = deterministic(tmp_path, sids["rt_csv"])
    for other in ("fast_csv", "fast_parquet"):
        got = deterministic(tmp_path, sids[other])
        for k in ref:
            pd.testing.assert_frame_equal(ref[k], got[k], obj=f"{other}:{k}")
    descs = {k: storage.read_table(tmp_path / "analysis" / s / "descriptors", as_str=False) for k, s in sids.items()}
    for other in ("fast_csv", "fast_parquet"):
        np.testing.assert_allclose(descs["rt_csv"][FEATURE_COLUMNS].to_numpy(float), descs[other][FEATURE_COLUMNS].to_numpy(float),
                                   rtol=0, atol=1e-12)
        assert list(descs["rt_csv"].descriptor_timestamp_ns) == list(descs[other].descriptor_timestamp_ns)
    meta = json.loads((tmp_path / "metadata" / sids["fast_parquet"] / "metadata.json").read_text())
    assert meta["versions"]["schema_version"] == "1.0" and meta["storage"]["format"] == "parquet"
    assert meta["simulation"]["execution"]["mode"] == "fast" and meta["simulation"]["execution"]["flow_control_timeouts"] == 0
    profiles = {v["profile"] for v in meta["fleet"]["vehicles"]}
    assert profiles == set(ABC) and all(v["switch_on_ns"] > 0 for v in meta["fleet"]["vehicles"])

    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from build_manifest import MANIFEST_FIELDS, build
    rows = build(tmp_path, "M5_EQ_")
    assert len(rows) == 3 and all(set(MANIFEST_FIELDS) <= set(r) for r in rows)
    fp = next(r for r in rows if r["session_id"] == sids["fast_parquet"])
    assert fp["storage_format"] == "parquet" and fp["generator_validation"] == "PASS" and fp["descriptor_validation"] == "PASS"
    assert fp["campaign_sizes"] == {"CT1": 2, "CT2": 1} and fp["configured_relative_delays_s"]["CT1"] == [0.0, 0.5]
