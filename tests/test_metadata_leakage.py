"""P1 metadata-leakage safeguards for fleetcan-sim-research-1.0 (research/adapter.py).

Synthetic tests deliberately inject prohibited metadata and check that (1) the adapter rejects/excludes it,
(2) descriptor values do not change when prohibited metadata changes, (3) ground truth is not reachable from the
detector loader, (4) graph construction cannot see campaign membership, (5) model inputs are exactly the 24 features.
Release-wide tests (marked `release`) scan every exported session; they are skipped when the release is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from analysis.descriptor_schema import FEATURE_COLUMNS
from detection.features import LeakageError
from research import adapter
from research.common import OBS_COLUMNS, RELEASE

RNG = np.random.default_rng(7)


def synthetic_obs(n_veh=3, n=400) -> pd.DataFrame:
    rows = []
    fi = 0
    for v in range(n_veh):
        t = np.cumsum(RNG.integers(1_000_000, 3_000_000, n))
        for k in range(n):
            dlc = int(RNG.integers(0, 9))
            rows.append({"frame_index": fi, "timestamp_ns": int(t[k]), "vehicle_id": f"vehicle_{v:03d}",
                         "can_id": f"0x{int(RNG.integers(0, 0x800)):03X}", "dlc": dlc,
                         "data": "".join(f"{b:02X}" for b in RNG.integers(0, 256, dlc))})
            fi += 1
    return pd.DataFrame(rows).sort_values(["timestamp_ns", "frame_index"]).reset_index(drop=True)


INJECT = {
    "scenario_id": lambda n: np.full(n, "S3"), "session_id": lambda n: np.full(n, "FCS10_N020_S3_x_R1"),
    "frame_uid": lambda n: [f"X-F{i:09d}" for i in range(n)], "capture_time_ns": lambda n: RNG.integers(0, 2**62, n),
    "frame_label": lambda n: RNG.choice(["benign", "malicious"], n), "attack_id": lambda n: RNG.choice(["None", "A1"], n),
    "campaign_id": lambda n: RNG.choice(["None", "C_S3"], n), "coordinated": lambda n: RNG.choice(["true", "false"], n),
    "attack_type": lambda n: RNG.choice(["none", "replay"], n), "configured_start_ns": lambda n: np.full(n, 10**10),
    "configuration_id": lambda n: np.full(n, "FCS10_N020_S3"), "route_group": lambda n: np.full(n, "G0"),
    "profile": lambda n: RNG.choice(list("ABC"), n), "fleet_size": lambda n: np.full(n, 20),
}


@pytest.mark.parametrize("field", sorted(INJECT))
def test_adapter_rejects_each_prohibited_field(field):
    obs = synthetic_obs()
    bad = obs.assign(**{field: INJECT[field](len(obs))})
    with pytest.raises(LeakageError):
        adapter.sanitize_observations(bad, "strict")
    assert list(adapter.sanitize_observations(bad, "exclude").columns) == list(obs.columns)


def test_unknown_fields_rejected_or_excluded():
    obs = synthetic_obs().assign(some_new_metadata=1)
    with pytest.raises(LeakageError):
        adapter.sanitize_observations(obs, "strict")
    assert "some_new_metadata" not in adapter.sanitize_observations(obs, "exclude").columns


def test_descriptors_invariant_to_prohibited_metadata():
    obs = synthetic_obs()
    ref = adapter.descriptors(obs)
    for _ in range(3):
        noisy = obs.assign(**{k: f(len(obs)) for k, f in INJECT.items()})
        got = adapter.descriptors(adapter.sanitize_observations(noisy, "exclude"))
        pd.testing.assert_frame_equal(ref, got)
    with pytest.raises(LeakageError):                        # and they cannot be passed silently
        adapter.descriptors(obs.assign(campaign_id="C_S3"))


def test_model_input_is_exactly_24_features():
    d = adapter.descriptors(synthetic_obs())
    m = adapter.model_input(d)
    assert m.shape == (len(d), 24) and m.dtype == np.float32
    for field in ("profile", "fleet_size", "label_majority_original", "campaign_ids", "session_id", "route_group"):
        x = d.assign(**{field: 1})
        with pytest.raises(LeakageError):
            adapter.model_input(x)
    # vehicle identity is a key column: allowed in the table, never in the matrix (renaming vehicles changes nothing)
    renamed = d.assign(vehicle_id=d.vehicle_id.map({v: f"veh_{i}" for i, v in enumerate(sorted(d.vehicle_id.unique()), 7)}))
    assert np.array_equal(adapter.model_input(renamed), m)
    from detection.features import model_matrix
    with pytest.raises(LeakageError):                        # original allow-list also rejects identity columns
        model_matrix(d[["vehicle_id", *FEATURE_COLUMNS]])


def test_graph_construction_cannot_access_campaign_membership():
    d = adapter.descriptors(synthetic_obs())
    for field in ("campaign_ids", "campaign_id", "coordinated", "attack_ids", "window_class", "malicious_frame_count"):
        with pytest.raises(LeakageError):
            adapter.graph_inputs(d.assign(**{field: "C1"}))
    e1 = adapter.temporal_edges(d, 0.5)
    shuffled_truth = d.sample(frac=1.0, random_state=1).sort_index()   # graph rule sees no truth: identical edges
    e2 = adapter.temporal_edges(shuffled_truth, 0.5)
    pd.testing.assert_frame_equal(e1, e2)
    assert set(adapter.graph_inputs(d).columns) == set(adapter.GRAPH_INPUT_COLUMNS)


def test_loader_only_opens_raw_parquet(tmp_path):
    with pytest.raises(LeakageError):
        adapter.observation_path(tmp_path / "x" / "ground_truth" / "..", "S")


release = pytest.mark.skipif(not (RELEASE / "data" / "raw_parquet").exists(), reason="release not exported")


@release
def test_release_observation_files_contain_only_allowed_columns():
    sessions = sorted(p.name for p in (RELEASE / "data" / "raw_parquet").iterdir() if p.is_dir())
    assert sessions
    for sid in sessions:
        assert pq.read_schema(RELEASE / "data" / "raw_parquet" / sid / "can_frames.parquet").names == OBS_COLUMNS
        head = (RELEASE / "data" / "raw_csv" / sid / "can_frames.csv.gz")
        import csv
        import gzip
        with gzip.open(head, "rt", newline="") as fh:
            assert next(csv.reader(fh)) == OBS_COLUMNS


@release
def test_release_descriptors_contain_no_labels_or_metadata():
    allowed = set(adapter.DESCRIPTOR_KEY_COLUMNS) | set(FEATURE_COLUMNS)
    for p in sorted((RELEASE / "data" / "descriptors").glob("*/descriptors.parquet")):
        cols = pq.read_schema(p).names
        assert set(cols) <= allowed and not set(cols) & adapter.PROHIBITED_FIELDS, p
        assert [c for c in cols if c in FEATURE_COLUMNS] == FEATURE_COLUMNS


@release
def test_release_ground_truth_and_metadata_physically_separate():
    for sid_dir in sorted((RELEASE / "data" / "raw_parquet").iterdir()):
        files = {p.name for p in sid_dir.iterdir()}
        assert files == {"can_frames.parquet"}, sid_dir
        assert (RELEASE / "data" / "ground_truth" / sid_dir.name / "frame_labels.parquet").exists()
        assert (RELEASE / "data" / "metadata" / sid_dir.name / "session.json").exists()
