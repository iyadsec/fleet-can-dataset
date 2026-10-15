"""CAN bus timing / arbitration model tests (must pass before any attack work)."""

import random
from dataclasses import dataclass

import pytest

from src.bus_model import (BusConfig, CanBusModel, count_stuff_bits, crc15, frame_bits,
                           utilization_bins, _bits)


@dataclass(frozen=True)
class F:
    sim_time_ns: int
    can_id: int
    data: bytes = bytes(8)
    tag: str = ""


US = 1_000


# --- frame duration ----------------------------------------------------------------------------
def test_unstuffed_and_worst_case_bit_counts():
    for n in range(9):
        assert frame_bits(0x123, bytes(n), "none") == 47 + 8 * n
        assert frame_bits(0x123, bytes(n), "worst_case") == 47 + 8 * n + (34 + 8 * n - 1) // 4


def test_exact_stuffing_is_between_bounds():
    rnd = random.Random(1)
    for _ in range(500):
        n = rnd.randint(0, 8)
        cid, data = rnd.randint(0, 0x7FF), bytes(rnd.getrandbits(8) for _ in range(n))
        b = frame_bits(cid, data, "exact")
        assert frame_bits(cid, data, "none") <= b <= frame_bits(cid, data, "worst_case")


def test_stuff_bit_rule_counts_inserted_bits_toward_next_run():
    assert count_stuff_bits([0] * 4) == 0
    assert count_stuff_bits([0] * 5) == 1
    assert count_stuff_bits([0] * 10) == 2
    assert count_stuff_bits([1] * 5 + [0] * 4) == 2   # stuffed 0 + four 0s = run of 5


def test_crc15_residue_is_zero():
    rnd = random.Random(2)
    for _ in range(100):
        bits = [rnd.getrandbits(1) for _ in range(rnd.randint(19, 83))]
        assert crc15(bits + _bits(crc15(bits), 15)) == 0


def test_flooding_frame_duration_at_500k():
    # ID 0x000 with 8 zero bytes: long runs of 0 => many stuff bits
    bits = frame_bits(0x000, bytes(8), "exact")
    m = CanBusModel(BusConfig(bitrate=500_000))
    m.submit(F(0, 0x000), "A")
    (tx,), _ = m.advance_until(1)
    assert tx.bits == bits and tx.end_ns - tx.start_ns == bits * 2000


# --- arbitration / ordering ----------------------------------------------------------------------
def test_lowest_id_wins_when_ready_together():
    m = CanBusModel(BusConfig())
    for cid, node in [(0x300, "A"), (0x100, "B"), (0x200, "C")]:
        m.submit(F(0, cid), node)
    txs, _ = m.drain()
    assert [t.frame.can_id for t in txs] == [0x100, 0x200, 0x300]
    assert all(b.start_ns == a.end_ns for a, b in zip(txs, txs[1:]))     # back-to-back, no overlap


def test_frame_arriving_during_transmission_waits_then_competes():
    m = CanBusModel(BusConfig())
    m.submit(F(0, 0x400), "A")               # starts at 0
    m.submit(F(10 * US, 0x300), "B")         # ready while bus busy
    m.submit(F(20 * US, 0x050), "C")         # ready later but higher priority
    txs, _ = m.drain()
    assert [t.frame.can_id for t in txs] == [0x400, 0x050, 0x300]
    assert txs[1].start_ns == txs[0].end_ns and txs[1].queue_delay_ns == txs[0].end_ns - 20 * US
    assert txs[2].queue_delay_ns > 0


def test_non_preemptive_and_idle_gaps():
    m = CanBusModel(BusConfig())
    m.submit(F(0, 0x700), "A")
    m.submit(F(1 * US, 0x001), "B")           # cannot pre-empt the running frame
    m.submit(F(10_000 * US, 0x100), "A")      # bus idle long before this
    txs, _ = m.drain()
    assert txs[0].frame.can_id == 0x700 and txs[1].start_ns == txs[0].end_ns
    assert txs[2].start_ns == 10_000 * US and txs[2].queue_delay_ns == 0


def test_node_queue_order_priority_vs_fifo():
    for order, expected in [("priority", [0x100, 0x200, 0x300]), ("fifo", [0x300, 0x200, 0x100])]:
        m = CanBusModel(BusConfig(node_queue_order=order))
        m.submit(F(0, 0x7FF), "X")                       # occupies bus so A's frames queue
        for i, cid in enumerate([0x300, 0x200, 0x100]):
            m.submit(F(1 + i, cid), "A")
        txs, _ = m.drain()
        assert [t.frame.can_id for t in txs[1:]] == expected


# --- queueing / drops ------------------------------------------------------------------------------
def test_queue_limit_drop_newest_and_drop_oldest():
    for policy, kept_tags in [("drop_newest", ["a0", "a1"]), ("drop_oldest", ["a2", "a3"])]:
        m = CanBusModel(BusConfig(queue_limit=2, drop_policy=policy, node_queue_order="fifo"))
        m.submit(F(0, 0x000), "BLOCK")                   # bus busy ~ 270 us
        for i in range(4):
            m.submit(F(1 + i, 0x100, tag=f"a{i}"), "A")
        txs, drops = m.drain()
        assert [t.frame.tag for t in txs if t.node == "A"] == kept_tags
        assert len(drops) == 2 and m.stats.dropped == 2
        assert all(d.reason.startswith("queue_full") for d in drops)


def test_saturation_delays_and_drops_lower_priority_traffic():
    cfg = BusConfig(queue_limit=8)
    m = CanBusModel(cfg)
    dur = frame_bits(0, bytes(8)) * 2000
    t_end = 100_000 * US
    for k in range(0, t_end, dur // 2):                 # attacker offers ~200 % load at ID 0x000
        m.submit(F(k, 0x000), "ATT")
    for k in range(0, t_end, 10_000 * US):              # 10 ms ECU message
        m.submit(F(k, 0x100), "ECU")
    txs, drops = m.advance_until(t_end)
    ecu_tx = [t for t in txs if t.node == "ECU"]
    assert not ecu_tx                                   # starved while the flood lasts
    assert any(d.node == "ECU" for d in drops) and any(d.node == "ATT" for d in drops)
    busy = sum(t.end_ns - t.start_ns for t in txs)
    assert busy / t_end > 0.99


# --- incremental processing ------------------------------------------------------------------------
def test_stepwise_advance_equals_one_shot():
    rnd = random.Random(7)
    frames = [(F(rnd.randint(0, 50_000) * US, rnd.choice([0x000, 0x100, 0x120, 0x200, 0x7FF]),
                 bytes(rnd.getrandbits(8) for _ in range(8)), str(i)), rnd.choice("ABC"))
              for i in range(3000)]
    frames.sort(key=lambda fn: fn[0].sim_time_ns)

    def schedule(step):
        m = CanBusModel(BusConfig(queue_limit=16))
        out, i = [], 0
        for t in range(0, 60_000 * US, step):
            while i < len(frames) and frames[i][0].sim_time_ns < t + step:
                m.submit(*frames[i]); i += 1
            out += [(x.frame.tag, x.start_ns) for x in m.advance_until(t + step)[0]]
        out += [(x.frame.tag, x.start_ns) for x in m.drain()[0]]
        return out

    assert schedule(10_000 * US) == schedule(60_000 * US) == schedule(1_000 * US)


def test_submit_behind_decision_horizon_rejected():
    m = CanBusModel(BusConfig())
    m.advance_until(10 * US)
    with pytest.raises(ValueError):
        m.submit(F(5 * US, 0x100), "A")


def test_disabled_model_is_ideal_bus():
    m = CanBusModel(BusConfig(enabled=False))
    m.submit(F(5, 0x100), "A"); m.submit(F(5, 0x050), "B")
    txs, _ = m.advance_until(10)
    assert all(t.start_ns == t.end_ns == 5 for t in txs)


# --- utilization ------------------------------------------------------------------------------------
def test_utilization_bins_split_intervals():
    u = utilization_bins([(50, 150), (300, 400)], 0, 400, 100)
    assert u == [0.5, 0.5, 0.0, 1.0]
    assert all(0 <= x <= 1 for x in u)


# --- frame expiry (Milestone 3) ---------------------------------------------------------------
def test_retain_policy_is_default_and_keeps_stale_frames():
    assert BusConfig().expiry_policy == "retain"
    m = CanBusModel(BusConfig(queue_limit=1000))                # large queue: no queue drops
    for k in range(0, 50_000 * US, 200 * US):            # attacker keeps the bus busy for 50 ms
        m.submit(F(k, 0x000), "ATT")
    m.submit(F(0, 0x100), "ECU")
    txs, drops = m.drain()
    ecu = [t for t in txs if t.node == "ECU"]
    assert len(ecu) == 1 and ecu[0].queue_delay_ns > 40_000 * US and not drops


def test_deadline_policy_expires_frames_at_exact_deadline():
    cfg = BusConfig(expiry_policy="deadline", max_frame_age_ms=5.0)
    m = CanBusModel(cfg)
    for k in range(0, 50_000 * US, 200 * US):
        m.submit(F(k, 0x000), "ATT")
    m.submit(F(1 * US, 0x100, tag="victim"), "ECU")
    txs, drops = m.drain()
    assert not [t for t in txs if t.node == "ECU"]
    (d,) = [d for d in drops if d.node == "ECU"]
    assert d.kind == "expired_drop" and d.drop_ns == 1 * US + 5_000 * US and d.reason == "expired_max_age"
    assert m.stats.expired_drops >= 1 and m.stats.queue_drops == 0
    assert all(t.queue_delay_ns <= 5_000 * US for t in txs)     # nothing transmitted after its deadline


def test_queue_and_expired_drops_counted_separately():
    m = CanBusModel(BusConfig(expiry_policy="deadline", max_frame_age_ms=2.0, queue_limit=2))
    m.submit(F(0, 0x000), "BLOCK")
    for i in range(5):
        m.submit(F(1 + i, 0x100, tag=f"a{i}"), "A")             # 2 queued, 3 rejected (queue full)
    txs, drops = m.drain()
    kinds = sorted(d.kind for d in drops)
    assert kinds.count("queue_drop") == 3 and m.stats.queue_drops == 3
    assert m.stats.expired_drops == kinds.count("expired_drop")


def test_deadline_stepwise_equals_one_shot():
    rnd = random.Random(11)
    frames = [(F(rnd.randint(0, 40_000) * US, rnd.choice([0x000, 0x100, 0x7FF]), bytes(8), str(i)), rnd.choice("AB"))
              for i in range(4000)]
    frames.sort(key=lambda fn: fn[0].sim_time_ns)

    def run(step):
        m = CanBusModel(BusConfig(expiry_policy="deadline", max_frame_age_ms=3.0, queue_limit=8))
        tx, dr, i = [], [], 0
        for t in range(0, 50_000 * US, step):
            while i < len(frames) and frames[i][0].sim_time_ns < t + step:
                m.submit(*frames[i]); i += 1
            a, b = m.advance_until(t + step)
            tx += [(x.frame.tag, x.start_ns) for x in a]
            dr += [(x.frame.tag, x.drop_ns, x.kind) for x in b]
        a, b = m.drain()
        return tx + [(x.frame.tag, x.start_ns) for x in a], sorted(dr + [(x.frame.tag, x.drop_ns, x.kind) for x in b])

    assert run(10_000 * US) == run(1_000 * US) == run(50_000 * US)


def test_deadline_requires_max_age():
    with pytest.raises(ValueError):
        BusConfig(expiry_policy="deadline")
