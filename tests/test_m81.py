"""Milestone 8.1: bounded-memory generation (generator 1.3.0) must reproduce the whole-session implementation exactly."""

import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from src import bus_stats, ground_truth, orchestrator, recorder, storage
from src.bus_model import BusConfig, BusDrop, BusTx
from src.can_bus import CapturedFrame
from src.can_generator import TxFrame
from src.chunked import ChunkedTable
from src.stream_recorder import FrameStream

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
HAVE_VCAN3 = all(Path(f"/sys/class/net/vcan{i}").exists() for i in range(3))


# ---------------------------------------------------------------- chunked writer
def _rows(n, rng):
    out = []
    for i in range(n):
        out.append({"i": i, "f": rng.random() * 1e3 - 500, "s": f"x{i}", "mixed": i if i % 7 else "",
                    "maybe": None if i % 5 == 0 else f"v{i}", "fl_or_int": 1.5 if i % 2 else 2.25})
    return out


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
@pytest.mark.parametrize("chunk", [1, 3, 7, 1000])
def test_chunked_table_equals_whole_table_write(tmp_path, fmt, chunk):
    rows = _rows(53, random.Random(chunk))
    cols = ["i", "f", "s", "mixed", "maybe", "fl_or_int"]
    storage.write_table(tmp_path / "a" / "t", cols, rows, fmt)
    t = ChunkedTable(tmp_path / "b" / "t", cols, fmt, chunk_rows=chunk)
    for r in rows:
        t.add(r)
    t.finalize()
    a = storage.read_table(tmp_path / "a" / "t")
    b = storage.read_table(tmp_path / "b" / "t")
    assert a.equals(b)
    if fmt == "parquet":
        sa, sb = pq.read_schema(storage.table_file(tmp_path / "a" / "t")), pq.read_schema(storage.table_file(tmp_path / "b" / "t"))
        assert sa.types == sb.types                                   # frozen typing rule: int64 / float64 / string
        assert pd.read_parquet(storage.table_file(tmp_path / "a" / "t")).equals(pd.read_parquet(storage.table_file(tmp_path / "b" / "t")))
        assert pq.ParquetFile(storage.table_file(tmp_path / "b" / "t")).num_row_groups == -(-53 // chunk)   # no single concatenated group
    assert t.max_buffered <= chunk
    assert not list((tmp_path / "b").glob("*.part"))                 # temporary file removed


def test_chunked_float_text_round_trip_is_exact(tmp_path):
    rng = np.random.default_rng(0)
    vals = list(rng.normal(size=500) * 10.0 ** rng.integers(-12, 12, size=500)) + [0.1, 1e-300, 123456789.123456789, -0.0]
    rows = [{"v": float(x)} for x in vals]
    t = ChunkedTable(tmp_path / "t", ["v"], "parquet", chunk_rows=17)
    t.extend(rows)
    t.finalize()
    got = pd.read_parquet(tmp_path / "t.parquet").v.to_numpy()
    assert np.array_equal(got, np.array(vals)) and pq.read_schema(tmp_path / "t.parquet").field("v").type == "double"


def test_chunked_empty_table_matches(tmp_path):
    storage.write_table(tmp_path / "a", ["x", "y"], [], "parquet")
    t = ChunkedTable(tmp_path / "b", ["x", "y"], "parquet", chunk_rows=4)
    t.finalize()
    assert pq.read_schema(tmp_path / "a.parquet") == pq.read_schema(tmp_path / "b.parquet")


def test_chunked_rejects_unknown_columns(tmp_path):
    t = ChunkedTable(tmp_path / "t", ["a"], "parquet", chunk_rows=2)
    with pytest.raises(ValueError):
        t.add({"a": 1, "b": 2})


# ---------------------------------------------------------------- streaming recorder vs whole-session recorder
class _Ctx:
    def __init__(self):
        self.replay_source_refs = {}


def _synthetic(n_per_ch=60, seed=0):
    rng = random.Random(seed)
    chans = ["vcan0", "vcan1", "vcan2"]
    recs, t = [], {c: 0 for c in chans}
    for k in range(n_per_ch):
        for i, ch in enumerate(chans):
            t[ch] += rng.randint(150_000, 900_000)
            attack = "A1" if (ch == "vcan1" and 20 <= k < 30) else None
            f = TxFrame(t[ch] - 100_000, f"vehicle_0{i + 1}", ch, rng.randint(0, 0x7FF), 8, bytes(rng.randrange(256) for _ in range(8)),
                        "M", source="attack_injection" if attack else "benign_generator", attack_id=attack,
                        attack_type="flooding" if attack else None, ref={} if attack else None)
            tx = BusTx(f, "ECU", f.sim_time_ns, t[ch] - 50_000, t[ch], 111)
            recs.append(recorder.TxRecord(tx, 0))
    recs.sort(key=lambda r: (r.tx.end_ns, r.frame.vehicle_id))          # send order = frozen sort order
    return recs, chans


def _caps(recs, chans, corrupt=None, extra=None):
    caps, seq = {c: [] for c in chans}, {c: 0 for c in chans}
    for r in recs:
        f, ch = r.frame, r.frame.channel
        data = f.data if r is not corrupt else bytes(8)
        caps[ch].append(CapturedFrame(ch, 10**9 + r.tx.end_ns, f.can_id, False, f.dlc, data, seq[ch]))
        seq[ch] += 1
    if extra:
        caps[extra].append(CapturedFrame(extra, 5, 0x123, False, 1, b"\x01", seq[extra]))
    return caps


@pytest.mark.parametrize("seed", range(4))
def test_stream_recorder_equals_batch_recorder(tmp_path, seed):
    recs, chans = _synthetic(seed=seed)
    corrupt = recs[17]
    caps = _caps(recs, chans, corrupt=corrupt, extra="vcan2")
    missing = recs[-1]                                              # never captured
    caps[missing.frame.channel] = [c for c in caps[missing.frame.channel] if c.capture_time_ns != 10**9 + missing.tx.end_ns]
    # whole-session reference
    pairs, unexpected, stats = recorder.associate({c: [r for r in recs if r.frame.channel == c] for c in chans}, caps, {c: 0 for c in chans})
    frows, seeds, uid_of = recorder.build_frame_rows(pairs, "S", "S1", None)
    lrows = ground_truth.frame_label_rows(seeds, "S")
    arows, _ = ground_truth.attack_frame_rows(pairs, uid_of)
    # streaming with random capture arrival (captures may arrive before or after their send record)
    tabs = {k: ChunkedTable(tmp_path / k, c, "parquet", chunk_rows=5) for k, c in
            (("frames", recorder.FRAME_COLUMNS), ("labels", ground_truth.LABEL_COLUMNS),
             ("attack_frames", ground_truth.ATTACK_FRAME_COLUMNS), ("replay", ground_truth.REPLAY_COLUMNS))}
    st = FrameStream("S", "S1", chans, None, tabs, _Ctx())
    rng = random.Random(seed)
    pending = {c: list(v) for c, v in caps.items()}
    for r in recs:
        st.on_sent(r)
        for c in chans:
            k = rng.randint(0, 3)
            st.on_captures(c, pending[c][:k])
            pending[c] = pending[c][k:]
        st._emit()
    s2 = st.finish({c: v for c, v in pending.items()}, {c: 0 for c in chans})
    for t in tabs.values():
        t.finalize()
    assert storage.read_table(tmp_path / "frames").equals(pd.DataFrame(frows).astype(str))
    assert storage.read_table(tmp_path / "labels").equals(pd.DataFrame(lrows).astype(str))
    assert storage.read_table(tmp_path / "attack_frames").equals(pd.DataFrame(arows, columns=ground_truth.ATTACK_FRAME_COLUMNS).astype(str))
    assert s2.per_channel == stats.per_channel and s2.total == stats.total
    assert [c.seq for c in st.unexpected_captures()] == [c.seq for c in unexpected]
    assert st.tally.captured == ground_truth_tally(pairs).captured


def ground_truth_tally(pairs):
    t = ground_truth.AttackTally()
    for r, _ in pairs:
        t.add(r.frame)
    return t


def test_stream_recorder_rejects_out_of_order_emission(tmp_path):
    recs, chans = _synthetic(n_per_ch=5)
    recs = recs[::-1]
    tabs = {k: ChunkedTable(tmp_path / k, c, "parquet", chunk_rows=50) for k, c in
            (("frames", recorder.FRAME_COLUMNS), ("labels", ground_truth.LABEL_COLUMNS),
             ("attack_frames", ground_truth.ATTACK_FRAME_COLUMNS), ("replay", ground_truth.REPLAY_COLUMNS))}
    st = FrameStream("S", "S1", chans, None, tabs, _Ctx())
    caps = _caps(recs, chans)
    with pytest.raises(RuntimeError, match="order"):
        for r in recs:
            st.on_sent(r)
            st.on_captures(r.frame.channel, [caps[r.frame.channel].pop(0)])
            st._emit()


def test_replay_source_uid_kept_only_until_replayed(tmp_path):
    ctx = _Ctx()
    o = TxFrame(1_000, "vehicle_01", "vcan0", 0x100, 2, b"\x01\x02", "M")
    o_tx = BusTx(o, "ECU", 1_000, 1_000, 2_000, 50)
    ctx.replay_source_refs[id(o)] = 1
    rp = TxFrame(9_000, "vehicle_01", "vcan0", 0x100, 2, b"\x01\x02", "M", source="attack_injection", attack_id="R",
                 attack_type="replay", ref={"original_frame": o, "original_sim_time_ns": 1_000, "original_tx_end_ns": 2_000,
                                            "source_vehicle": "vehicle_01", "target_vehicle": "vehicle_01", "original_payload": "0102"})
    rp_tx = BusTx(rp, "ATTACKER", 9_000, 9_000, 10_000, 50)
    tabs = {k: ChunkedTable(tmp_path / k, c, "parquet", chunk_rows=1) for k, c in
            (("frames", recorder.FRAME_COLUMNS), ("labels", ground_truth.LABEL_COLUMNS),
             ("attack_frames", ground_truth.ATTACK_FRAME_COLUMNS), ("replay", ground_truth.REPLAY_COLUMNS))}
    st = FrameStream("S", "S3", ["vcan0"], None, tabs, ctx)
    for tx in (o_tx, rp_tx):
        st.on_sent(recorder.TxRecord(tx, 0))
        st.on_captures("vcan0", [CapturedFrame("vcan0", tx.end_ns, tx.frame.can_id, False, 2, tx.frame.data, 0)])
        st._emit()
    st.finish({}, {})
    for t in tabs.values():
        t.finalize()
    rm = storage.read_table(tmp_path / "replay")
    assert list(rm.original_frame_uid) == ["S-F000000000"] and list(rm.replay_frame_uid) == ["S-F000000001"]
    assert ctx.replay_source_refs == {} and st.uid_of_source == {}       # released: memory bounded by pending replays


def test_replay_offer_selects_exactly_the_old_scan_selection():
    from src.attack_engine import AttackContext, Replay
    ctx = AttackContext({})
    a = Replay("R1", "replay", "vehicle_01", "vcan0", 3_000_000_000, 0, {"window_ns": [1_000_000_000, 2_000_000_000],
               "time_offset_ms": 0.25}, 1, np.random.default_rng(1))
    a.setup(ctx)
    rng = random.Random(2)
    txs = []
    t = 0
    for _ in range(3000):
        t += rng.randint(100_000, 2_000_000)
        src = "benign_generator" if rng.random() < 0.9 else "attack_injection"
        f = TxFrame(t, rng.choice(["vehicle_01", "vehicle_02"]), "vcan0", rng.randint(0, 0x7FF), 1, b"\x00", "M", source=src)
        txs.append(BusTx(f, "ECU", t, t, t + 100_000, 50))
    for k in range(0, len(txs), 37):
        ctx.record_transmitted(txs[k:k + 37])
    old = [(a.start_ns + (tx.end_ns - a.w0) + a.offset_ns, tx) for tx in txs
           if tx.frame.vehicle_id == "vehicle_01" and a.w0 <= tx.end_ns < a.w1 and tx.frame.source == "benign_generator"]
    assert list(a._queue) == old and len(ctx.replay_source_refs) == len(old)


# ---------------------------------------------------------------- incremental bus statistics
def test_bus_stats_accumulator_equals_whole_session(tmp_path, monkeypatch):
    monkeypatch.setattr(bus_stats, "SPILL_VALUES", 7)                 # force many spill flushes
    rng = random.Random(5)

    class _Bus:
        def __init__(self):
            self.cfg = BusConfig(expiry_policy="deadline", max_frame_age_ms=100.0)
            self.stats = type("S", (), {"max_queue_len": {"ECU": 3}, "same_id_contention": 0})()

    class _A:
        def __init__(self, aid, ch, s, e):
            self.attack_id, self.channel, self.start_ns, self.end_ns, self.campaign_id = aid, ch, s, e, None

    buses = {"vcan0": _Bus(), "vcan1": _Bus()}
    attacks = [_A("A", "vcan0", 2_000_000_000, 3_000_000_000), _A("B", "vcan1", 50_000_000, 400_000_000)]
    all_txs, all_drops = {c: [] for c in buses}, {c: [] for c in buses}
    acc = bus_stats.BusStatsAccumulator(buses, attacks, orchestrator.UTIL_BIN_NS, tmp_path / "spill")
    for ch in buses:
        t = 0
        for _ in range(4000):
            ready = t + rng.randint(0, 900_000)
            start = ready + rng.choice([0, 0, rng.randint(1, 300_000)])
            end = start + rng.randint(100_000, 260_000)
            t = end
            att = rng.random() < 0.2
            f = TxFrame(ready, "v", ch, 0, 8, b"\x00" * 8, "M", attack_id="A" if att else None)
            tx = BusTx(f, "ECU", ready, start, end, 120)
            all_txs[ch].append(tx)
            if rng.random() < 0.01:
                all_drops[ch].append(BusDrop(f, "ECU", ready, ready + 100_000_000, "expired (deadline)" if rng.random() < .5 else "queue_full"))
        for k in range(0, len(all_txs[ch]), 113):
            acc.add(ch, all_txs[ch][k:k + 113])

    class _E:
        pass
    eng = _E()
    eng.attacks = attacks
    want = orchestrator.bus_statistics(None, buses, all_txs, all_drops, eng, 3_100_000_000)
    got = acc.finalize(3_100_000_000, all_drops)
    acc.cleanup()
    assert json.dumps(got, sort_keys=True, default=str) == json.dumps(want, sort_keys=True, default=str)
    assert not list((tmp_path / "spill").glob("*"))


# ---------------------------------------------------------------- live runs (SUMO + vCAN)
def _campaign_cfg(tmp_path):
    from src.config import load_experiment
    camp = tmp_path / "c.yaml"
    camp.write_text("campaigns:\n  - campaign_id: C_T\n    attack_type: replay\n    coordinated: true\n"
                    "    targets: [vehicle_01, vehicle_02, vehicle_03]\n    start_s: 2.0\n    delay_pattern: fixed\n"
                    "    relative_delays_s: [0, 0.3, 0.6]\n    params: {window_relative_s: [-1.0, 0.0], source_vehicle: self, time_offset_ms: 0.25}\n")
    exp = tmp_path / "e.yaml"
    exp.write_text((ROOT / "config/m51/test/experiment_saturation.yaml").read_text()
                   .replace("scenario_id: S1", "scenario_id: S3").replace("duration_s: 3.5", "duration_s: 4.2")
                   .replace("attacks_config: config/m51/test/attacks_saturation.yaml", "attacks_config: config/attacks.yaml")
                   .replace("campaigns_config: config/campaigns.yaml", f"campaigns_config: {camp}"))
    return load_experiment(exp)


@pytest.mark.skipif(not HAVE_VCAN3, reason="vcan0..vcan2 not up")
@pytest.mark.parametrize("kind", ["replay_campaign", "saturation"])
def test_chunk_size_independence_live(tmp_path, kind):
    """Chunk boundaries every few rows (inside attacks, replay windows, campaign starts/ends) vs one big chunk."""
    from compare_sessions import compare
    from src.config import load_experiment
    out = {}
    for name, chunk in (("small", 7), ("large", 1_000_000)):
        cfg = _campaign_cfg(tmp_path) if kind == "replay_campaign" else load_experiment("config/m51/test/experiment_saturation.yaml")
        cfg.output_root = tmp_path
        sid, overall = orchestrator.run(cfg, session_id=f"T_{kind}_{name}", chunk_rows=chunk)
        assert overall == "PASS"
        out[name] = sid
    res = compare(tmp_path, out["small"], out["large"], frames_only=False)
    assert all(v.startswith("IDENTICAL") for v in res.values()), res
    b = [json.loads((tmp_path / "metadata" / s / "bus_stats.json").read_text()) for s in out.values()]
    assert b[0] == b[1]
    if kind == "replay_campaign":
        rm = storage.read_table(tmp_path / "ground_truth" / out["small"] / "replay_map")
        assert len(rm) > 0 and (rm.original_frame_uid != "").all()
    else:
        d = storage.read_table(tmp_path / "ground_truth" / out["small"] / "dropped_frames")
        assert (d.drop_kind == "queue_drop").any() and (d.drop_kind == "expired_drop").any()
    assert not list(tmp_path.rglob("*.part")) and not (tmp_path / ".incomplete" / out["small"]).exists()


@pytest.mark.skipif(not HAVE_VCAN3, reason="vcan0..vcan2 not up")
def test_interrupted_run_leaves_no_session_and_rerun_is_safe(tmp_path, monkeypatch):
    from src.config import load_experiment
    from src.sumo_controller import SumoController
    real_step = SumoController.step
    calls = {"n": 0}

    def failing_step(self):
        calls["n"] += 1
        if calls["n"] > 120:
            raise RuntimeError("simulated interruption")
        return real_step(self)

    cfg = load_experiment("config/experiment.yaml")
    cfg.duration_s, cfg.output_root, cfg.output_format = 3.0, tmp_path, "parquet"
    monkeypatch.setattr(SumoController, "step", failing_step)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        orchestrator.run(cfg, session_id="T_CRASH")
    assert (tmp_path / ".incomplete" / "T_CRASH").exists()
    assert not any((tmp_path / d / "T_CRASH").exists() for d in orchestrator.SESSION_DIRS)   # not a session
    monkeypatch.setattr(SumoController, "step", real_step)
    cfg = load_experiment("config/experiment.yaml")
    cfg.duration_s, cfg.output_root, cfg.output_format = 3.0, tmp_path, "parquet"
    sid, overall = orchestrator.run(cfg, session_id="T_CRASH")                       # same id: safe re-run
    assert overall == "PASS" and (tmp_path / "metadata" / sid / "metadata.json").exists()
    assert not (tmp_path / ".incomplete" / sid).exists() and not list(tmp_path.rglob("*.part"))
    with pytest.raises(SystemExit, match="already exists"):
        orchestrator.run(cfg, session_id="T_CRASH")                                  # never overwrite


# ---------------------------------------------------------------- data-backed equivalence
def _has(*sids):
    return all((DATA / "metadata" / s / "metadata.json").exists() for s in sids)


@pytest.mark.skipif(not _has("FC11_N05_S3-mixed_malfunction_s02_small_R3"), reason="frozen dataset not present")
def test_grouped_validator_equals_whole_session_validator():
    from src import validator
    from tests.reference import validator_1_2 as ref
    sid = "FC11_N05_S3-mixed_malfunction_s02_small_R3"
    norm = lambda r: json.loads(json.dumps({"checks": r.checks, "info": r.info}, default=str))  # noqa: E731
    old = norm(ref.validate(sid, DATA))
    for g in (1, 2, 5):
        assert norm(validator.validate(sid, DATA, group_size=g)) == old


@pytest.mark.skipif(not _has("FC11_N10_S3-mixed_replay_s05_small_R2"), reason="frozen dataset not present")
def test_grouped_analysis_equals_stored_outputs(tmp_path):
    from analysis.run_session import analyse
    sid = "FC11_N10_S3-mixed_replay_s05_small_R2"
    for g in (1, 4):
        analyse(sid, DATA, out_root=tmp_path / f"g{g}", group_size=g)
        for t in ("descriptors", "descriptor_labels", "temporal_alignment"):
            a = pd.read_parquet(DATA / "analysis" / sid / f"{t}.parquet")
            b = pd.read_parquet(tmp_path / f"g{g}" / sid / f"{t}.parquet")
            assert a.equals(b), t
        assert json.loads((DATA / "analysis" / sid / "descriptor_validation.json").read_text()) == \
            json.loads((tmp_path / f"g{g}" / sid / "descriptor_validation.json").read_text())


@pytest.mark.skipif(not _has("FC11_N10_S3_replay_s10_large_R4", "M81_REG_FC11_N10_S3_replay_s10_large_R4"), reason="regression run absent")
def test_n10_regression_against_frozen_session():
    from m81_memory import compare_full
    assert compare_full("FC11_N10_S3_replay_s10_large_R4", "M81_REG_FC11_N10_S3_replay_s10_large_R4")["identical"]


@pytest.mark.skipif(not _has("M81_S0_N100_D120"), reason="M8.1 N=100 120 s run absent")
def test_n100_long_session_ids_and_uids():
    fr = pq.read_table(DATA / "raw/M81_S0_N100_D120/can_frames.parquet", columns=["frame_uid", "vehicle_id", "channel"])
    uids = fr.column("frame_uid").to_numpy(zero_copy_only=False)
    assert len(set(uids)) == len(uids) and uids[-1] == f"M81_S0_N100_D120-F{len(uids) - 1:09d}"   # dense, no gaps
    v = set(fr.column("vehicle_id").to_pylist())
    assert v == {f"vehicle_{i:03d}" for i in range(1, 101)}
    assert not (DATA / ".incomplete" / "M81_S0_N100_D120").exists()
