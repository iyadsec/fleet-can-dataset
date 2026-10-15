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
