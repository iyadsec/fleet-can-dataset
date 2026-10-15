"""Integration tests that need SUMO and live vCAN interfaces (skipped if vcan0..2 are absent)."""

import json
from pathlib import Path

import pandas as pd
import pytest

from src.config import load_experiment
from src.orchestrator import run
from src.sumo_controller import SumoController

HAVE_VCAN = all(Path(f"/sys/class/net/vcan{i}").exists() for i in range(3))


def test_sumo_traci_reads_three_vehicles_on_one_clock():
    cfg = load_experiment("config/experiment.yaml")
    s = SumoController(str(cfg.sumo_config), cfg.step_length_s, cfg.seed, label="test")
    s.start()
    try:
        seen = {}
        for _ in range(200):
            for st in s.step():
                seen.setdefault(st.sim_time_ns, set()).add(st.vehicle_id)
        common = [t for t, v in seen.items() if v == {"vehicle_01", "vehicle_02", "vehicle_03"}]
        assert len(common) >= 190                     # all three present at the same SUMO times
    finally:
        s.close()


@pytest.mark.skipif(not HAVE_VCAN, reason="vcan0..vcan2 not up (sudo scripts/setup_vcan.sh 3)")
def test_end_to_end_short_run(tmp_path):
    cfg = load_experiment("config/experiment.yaml")
    cfg.duration_s = 3.0
    cfg.output_root = tmp_path
    sid, overall = run(cfg, session_id="TEST_E2E")
    assert overall == "PASS"
    f = pd.read_csv(tmp_path / "raw" / sid / "can_frames.csv")
    assert set(f.vehicle_id) == {"vehicle_01", "vehicle_02", "vehicle_03"}
    meta = json.loads((tmp_path / "metadata" / sid / "metadata.json").read_text())
    assert meta["transport_stats"]["total"]["missing"] == 0


@pytest.mark.skipif(not HAVE_VCAN, reason="vcan0..vcan2 not up (sudo scripts/setup_vcan.sh 3)")
def test_benign_contention_follows_id_priority_and_back_to_back_timing(tmp_path):
    """All ECU offsets 0: frames ready together must leave in ID order, back-to-back, with
    queue delay = sum of the durations of the higher-priority frames ahead of them."""
    cfg = load_experiment("config/bus_check/experiment_sync.yaml")
    cfg.duration_s, cfg.output_root = 2.0, tmp_path
    sid, overall = run(cfg, session_id="TEST_SYNC")
    assert overall == "PASS"
    f = pd.read_csv(tmp_path / "raw" / sid / "can_frames.csv")
    f["id"] = f.can_id.map(lambda s: int(s, 16))
    checked = 0
    for (v, t), g in f.groupby(["vehicle_id", "sim_time_ns"]):
        if len(g) < 2:
            continue
        g = g.sort_values("tx_start_ns")
        assert list(g.id) == sorted(g.id)                                   # priority order
        assert g.queue_delay_us.iloc[0] == 0                               # winner starts at once
        assert (g.tx_start_ns.values[1:] == g.tx_end_ns.values[:-1]).all()  # back-to-back
        assert g.queue_delay_us.iloc[-1] * 1e3 == pytest.approx((g.tx_end_ns - g.tx_start_ns).iloc[:-1].sum())
        assert g.capture_time_ns.is_monotonic_increasing                   # vCAN capture keeps bus order
        checked += 1
    assert checked > 100


@pytest.mark.skipif(not HAVE_VCAN, reason="vcan0..vcan2 not up (sudo scripts/setup_vcan.sh 3)")
def test_end_to_end_mixed_campaign_over_vcan(tmp_path):
    """Short live run: coordinated C201 (vehicle_01+02, 0.5 s stagger) + independent C202 (vehicle_03)."""
    cfg = load_experiment("config/m3/experiment_s3_mixed_replay.yaml")
    for c in cfg.campaigns:                       # shrink timeline: replay windows [start-1 s, start)
        c["start_s"] = 2.0 if c["campaign_id"] == "C201" else 2.3
        c["params"]["window_relative_s"] = [-1.0, 0.0]
    cfg.duration_s, cfg.output_root = 4.5, tmp_path
    sid, overall = run(cfg, session_id="TEST_M3_MIXED")
    assert overall == "PASS"
    m = pd.read_csv(tmp_path / "ground_truth" / sid / "campaign_membership.csv")
    assert dict(zip(m.vehicle_id, m.campaign_id)) == {"vehicle_01": "C201", "vehicle_02": "C201", "vehicle_03": "C202"}
    assert list(m.relative_delay_ns) == [0, 500_000_000, 0]
    lab = pd.read_csv(tmp_path / "ground_truth" / sid / "frame_labels.csv", keep_default_na=False)
    mal = lab[lab.frame_label == "malicious"]
    assert set(mal.campaign_id) == {"C201", "C202"} and set(lab[lab.frame_label == "benign"].campaign_id) == {"None"}

    # Milestone 4: analysis layer on the live session; ground truth only in the separate labels file
    from analysis.descriptor_schema import FEATURE_COLUMNS, PROHIBITED_FIELDS
    from analysis.run_session import analyse
    r = analyse(sid, tmp_path)
    assert r["overall"] == "PASS"
    d = pd.read_csv(tmp_path / "analysis" / sid / "descriptors.csv")
    dl = pd.read_csv(tmp_path / "analysis" / sid / "descriptor_labels.csv", keep_default_na=False)
    assert not set(d.columns) & PROHIBITED_FIELDS and len([c for c in d.columns if c in FEATURE_COLUMNS]) == 24
    assert set(dl.descriptor_id) == set(d.descriptor_id) and {"C201", "C202"} <= set(dl.campaign_ids)
