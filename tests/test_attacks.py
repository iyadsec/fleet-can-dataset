"""Attack engine unit tests: run attacks against vehicle_01 through the generator and bus model
in memory (no SUMO, no vCAN), then check frames and ground truth."""

import pytest

from src.attack_engine import AttackConfigError, attack_seed, build_attacks
from src.bus_model import CanBusModel
from src.can_generator import VehicleCanGenerator, load_dbc
from src.config import load_experiment
from src.ground_truth import attack_frame_rows, attack_rows, frame_label_rows
from src.recorder import TxRecord
from src.sumo_controller import VehicleState

CFG = load_experiment("config/experiment.yaml")
PROF = CFG.profiles["model_A"]
BY_VEHICLE = {a.vehicle_id: CFG.profiles[a.profile] for a in CFG.fleet}
STEP = 10_000_000
S = 1_000_000_000


def simulate(attacks, seconds=4.0, speed=lambda t: 12.0):
    """Benign vehicle_01 + attacks -> bus model. Returns (engine, txs, drops)."""
    eng = build_attacks(attacks, CFG.fleet, BY_VEHICLE, seed=42)
    gen = VehicleCanGenerator("vehicle_01", "vcan0", PROF)
    bus = CanBusModel(PROF.bus, "vcan0")
    txs, drops = [], []
    for k in range(1, int(seconds * 100) + 1):
        t0 = k * STEP
        st = VehicleState(t0, "vehicle_01", speed(t0), 0.0, 0, 0, 90.0, "l", "e", "r")
        frames = eng.process(gen.frames_for_step(st, STEP), t0, t0 + STEP, {"vehicle_01"})
        for fr in frames:
            bus.submit(fr, fr.node)
        t, d = bus.advance_until(t0 + STEP)
        eng.observe(t)
        txs += t
        drops += d
    t, d = bus.drain()
    eng.observe(t)
    return eng, txs + t, drops + d


def att(aid, typ, start=1.0, dur=1.0, **params):
    a = {"attack_id": aid, "type": typ, "vehicle": "vehicle_01", "start_s": start, "params": params}
    if typ != "replay":
        a["duration_s"] = dur
    return a


def attack_frames(txs, aid):
    return [t for t in txs if t.frame.attack_id == aid]


# --- configuration / seeds ---------------------------------------------------------------------
def test_config_errors_are_rejected():
    with pytest.raises(AttackConfigError):
        build_attacks([att("X", "teleport")], CFG.fleet, BY_VEHICLE, 42)
    with pytest.raises(AttackConfigError):
        build_attacks([att("X", "flooding", rate_hz=100), att("X", "flooding", rate_hz=100)], CFG.fleet, BY_VEHICLE, 42)
    with pytest.raises(AttackConfigError):
        build_attacks([{**att("X", "flooding", rate_hz=100), "campaign_id": "C001"}], CFG.fleet, BY_VEHICLE, 42)
    with pytest.raises(AttackConfigError):     # 900 km/h exceeds the DBC range of VehicleSpeed (max 655.35)
        simulate([att("M", "malfunction", mode="inject", message="VEHICLE_SPEED", signal="VehicleSpeed",
                      manipulation={"strategy": "constant", "value": 900.0})])


def test_attack_seed_depends_on_experiment_seed_and_attack_id():
    assert attack_seed(42, "A1") == attack_seed(42, "A1")
    assert attack_seed(42, "A1") != attack_seed(43, "A1") != attack_seed(42, "A2")


# --- flooding -------------------------------------------------------------------------------------
def test_flooding_generates_exact_grid_and_passes_through_bus():
    eng, txs, drops = simulate([att("F", "flooding", start=1.0, dur=1.0, can_id=0, rate_hz=1000, payload="zeros")])
    fl = attack_frames(txs, "F")
    a = eng.attacks[0]
    assert a.generated == 1000 == len(fl) + len([d for d in drops if d.frame.attack_id == "F"])
    gens = sorted(t.frame.sim_time_ns for t in fl)
    assert gens[0] == 1 * S and gens[-1] == 2 * S - 1_000_000
    assert all(t.frame.can_id == 0 and t.frame.data == bytes(8) and t.node == "ATTACKER" for t in fl)
    assert all(t.start_ns >= t.ready_ns for t in fl)


def test_flooding_load_increases_benign_delay_and_saturation_drops():
    def benign_delay(rate):
        eng, txs, drops = simulate([att("F", "flooding", start=1.0, dur=2.0, can_id=0, rate_hz=rate)], seconds=3.5)
        ben = [t.queue_delay_ns for t in txs if not t.frame.attack_id and 1 * S <= t.ready_ns < 3 * S]
        return sum(ben) / max(1, len(ben)), sum(1 for d in drops if not d.frame.attack_id)
    low, med, over = benign_delay(400), benign_delay(2000), benign_delay(6000)
    assert low[0] < med[0] < over[0]
    assert low[1] == med[1] == 0 and over[1] > 0      # only over-saturation drops legitimate frames


# --- fuzzy ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["id", "payload", "both"])
def test_fuzzy_modes_randomise_only_what_is_configured(mode):
    p = {"mode": mode, "rate_hz": 500, "id_range": [0x300, 0x3FF], "dlc": 8}
    if mode == "payload":
        p["can_id"] = 0x120
    eng, txs, _ = simulate([att("Z", "fuzzy", **p)])
    fz = attack_frames(txs, "Z")
    ids, payloads = {t.frame.can_id for t in fz}, {t.frame.data for t in fz}
    if mode == "id":
        assert payloads == {bytes(8)} and len(ids) > 50 and all(0x300 <= i <= 0x3FF for i in ids)
    elif mode == "payload":
        assert ids == {0x120} and len(payloads) == len(fz)
    else:
        assert len(ids) > 50 and len(payloads) > 400
    assert all(t.frame.ref["fuzzy_mode"] == mode for t in fz)


def test_fuzzy_is_reproducible_with_the_same_seed():
    a = [(t.frame.can_id, t.frame.data) for t in attack_frames(simulate([att("Z", "fuzzy", mode="both", rate_hz=300)])[1], "Z")]
    b = [(t.frame.can_id, t.frame.data) for t in attack_frames(simulate([att("Z", "fuzzy", mode="both", rate_hz=300)])[1], "Z")]
    c = [(t.frame.can_id, t.frame.data) for t in attack_frames(simulate([att("Y", "fuzzy", mode="both", rate_hz=300)])[1], "Y")]
    assert a == b and a != c


# --- malfunction ----------------------------------------------------------------------------------
def test_malfunction_inject_carries_wrong_signal_with_legitimate_context():
    eng, txs, _ = simulate([att("M", "malfunction", mode="inject", message="VEHICLE_SPEED", signal="VehicleSpeed",
                                manipulation={"strategy": "constant", "value": 250.0}, rate_hz=100)])
    msg = load_dbc(PROF.dbc).get_message_by_name("VEHICLE_SPEED")
    mf = attack_frames(txs, "M")
    assert len(mf) == 100
    for t in mf:
        d = msg.decode(t.frame.data)
        assert t.frame.can_id == 0x120 and d["VehicleSpeed"] == pytest.approx(250.0)
        assert t.frame.ref["expected_value"] == pytest.approx(12.0 * 3.6, abs=0.01)
        assert t.frame.ref["malicious_value"] == pytest.approx(250.0)


def test_malfunction_replace_alters_only_the_target_signal_and_records_original():
    eng, txs, _ = simulate([att("M", "malfunction", mode="replace", message="ENGINE_STATUS", signal="EngineRPM",
                                manipulation={"strategy": "scale", "factor": 3.0})])
    msg = load_dbc(PROF.dbc).get_message_by_name("ENGINE_STATUS")
    mod = attack_frames(txs, "M")
    assert len(mod) == 100 and all(t.frame.source == "attack_modification" and t.node == "ECM" for t in mod)
    for t in mod:
        new, old = msg.decode(t.frame.data), msg.decode(bytes.fromhex(t.frame.ref["original_payload"]))
        assert new["EngineRPM"] == pytest.approx(3 * old["EngineRPM"], abs=0.25)
        assert new["Gear"] == old["Gear"] and new["Counter"] == old["Counter"]
    benign_ecm = [t for t in txs if t.frame.can_id == 0x100 and not t.frame.attack_id]
    assert all(not (1 * S <= t.frame.sim_time_ns < 2 * S) for t in benign_ecm)    # replaced, not duplicated


def test_malfunction_ramp_follows_configured_line():
    eng, txs, _ = simulate([att("M", "malfunction", mode="inject", message="STEERING", signal="SteeringAngle",
                                manipulation={"strategy": "ramp", "from": 0.0, "to": 90.0}, rate_hz=50)])
    msg = load_dbc(PROF.dbc).get_message_by_name("STEERING")
    for t in attack_frames(txs, "M"):
        want = 90.0 * (t.frame.sim_time_ns - S) / S
        assert msg.decode(t.frame.data)["SteeringAngle"] == pytest.approx(want, abs=0.05)


# --- replay ---------------------------------------------------------------------------------------
def test_replay_reinjects_observed_window_with_exact_shift():
    eng, txs, _ = simulate([att("R", "replay", start=2.5, window_s=[1.0, 1.5], time_offset_ms=0.25)], seconds=3.5)
    rp = attack_frames(txs, "R")
    observed = [t for t in txs if not t.frame.attack_id and 1 * S <= t.end_ns < 1.5 * S]
    assert len(rp) == len(observed) > 0
    by_orig = {id(t.frame): t for t in observed}
    for t in rp:
        o = by_orig[id(t.frame.ref["original_frame"])]
        assert t.frame.data == o.frame.data and t.frame.can_id == o.frame.can_id
        assert t.frame.sim_time_ns == int(2.5 * S) + (o.end_ns - S) + 250_000


def test_replay_window_must_precede_start():
    with pytest.raises(AttackConfigError):
        build_attacks([att("R", "replay", start=1.0, window_s=[0.5, 2.0])], CFG.fleet, BY_VEHICLE, 42)


# --- ground truth ---------------------------------------------------------------------------------
def test_attack_ground_truth_counts_and_labels():
    eng, txs, drops = simulate([att("F", "flooding", start=1.0, dur=1.0, can_id=0, rate_hz=6000),
                                att("R", "replay", start=2.5, window_s=[0.2, 0.7])], seconds=3.5)
    pairs = [(TxRecord(t, 0), None) for t in txs]
    uid = {id(t.frame): f"U{i}" for i, t in enumerate(txs)}
    rows = {r["attack_id"]: r for r in attack_rows(eng, pairs, drops, "S1", "sess")}
    f = rows["F"]
    assert f["generated"] == 6000 == f["transmitted"] + f["dropped"] and f["dropped"] > 0
    assert f["captured"] == f["transmitted"] and f["campaign_id"] == "None"
    detail, replay = attack_frame_rows(pairs, uid)
    assert len(replay) == rows["R"]["transmitted"] and all(r["original_frame_uid"] for r in replay)
    labels = frame_label_rows([{"frame_uid": uid[id(t.frame)], "frame": t.frame} for t in txs], "sess")
    assert {l["frame_label"] for l in labels if l["attack_id"] == "None"} == {"benign"}
    assert {l["frame_label"] for l in labels if l["attack_id"] != "None"} == {"malicious"}
