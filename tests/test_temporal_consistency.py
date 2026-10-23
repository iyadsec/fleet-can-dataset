"""P1 temporal handling: window boundaries, descriptor timestamps, ordering, tie-breaking, window labelling.

Rules under test (original FLEET-GUARD, see documentation/DESCRIPTOR_COMPATIBILITY.md):
  window size 100 frames, stride 50, windows start at frame 0, trailing partial window dropped;
  descriptor timestamp = observable timestamp (timestamp_ns = bus tx_end) of the window's LAST frame;
  per-vehicle observable order = timestamp_ns, ties broken by frame_index;
  original window label = majority of frame labels, tie -> malicious (malicious_frame_count >= 50).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from research import adapter
from research.common import RELEASE, STRIDE, WINDOW
from research.descriptors import window_starts


def trace(n, vid="vehicle_001", t0=0, step=1_000_000):
    return pd.DataFrame({"frame_index": np.arange(n), "timestamp_ns": t0 + np.arange(n) * step, "vehicle_id": vid,
                         "can_id": "0x100", "dlc": 8, "data": "0011223344556677"})


@pytest.mark.parametrize("n,expected", [(99, 0), (100, 1), (149, 1), (150, 2), (199, 2), (200, 3), (1000, 19)])
def test_window_count_and_partial_window_dropped(n, expected):
    assert len(window_starts(n)) == expected
    d = adapter.descriptors(trace(n))
    assert len(d) == expected
    if expected:
        assert (d.first_frame_index.values == np.arange(expected) * STRIDE).all()
        assert (d.last_frame_index.values == np.arange(expected) * STRIDE + WINDOW - 1).all()


def test_descriptor_timestamp_is_last_frame_timestamp():
    tr = trace(500, t0=123, step=777_000)
    d = adapter.descriptors(tr)
    assert (d.descriptor_timestamp_ns.values == tr.timestamp_ns.values[d.last_frame_index.values]).all()
    assert (d.window_start_ns.values == tr.timestamp_ns.values[d.first_frame_index.values]).all()
    assert np.isclose(d.mean_inter_arrival_time.values, 777e-6).all()


def test_ordering_and_deterministic_tie_break():
    tr = trace(300)
    tr.loc[10, "timestamp_ns"] = tr.loc[9, "timestamp_ns"]          # tie: frame_index decides
    shuffled = tr.sample(frac=1.0, random_state=3)
    d1, d2 = adapter.descriptors(tr), adapter.descriptors(shuffled)
    pd.testing.assert_frame_equal(d1, d2)


def test_windows_never_mix_vehicles():
    tr = pd.concat([trace(250, "vehicle_001"), trace(250, "vehicle_002", t0=500_000)], ignore_index=True)
    tr["frame_index"] = np.arange(len(tr))
    d = adapter.descriptors(tr)
    for vid, g in d.groupby("vehicle_id"):
        idx = tr.set_index("frame_index").loc[g.first_frame_index, "vehicle_id"]
        assert (idx.values == vid).all()


def test_timestamps_use_observable_time_only():
    tr = trace(300)
    with pytest.raises(Exception):
        adapter.descriptors(tr.assign(capture_time_ns=tr.timestamp_ns + 5))
    with pytest.raises(Exception):
        adapter.descriptors(tr.assign(configured_start_ns=10**10))


@pytest.mark.parametrize("k,majority,any_", [(0, 0, 0), (1, 0, 1), (5, 0, 1), (49, 0, 1), (50, 1, 1), (100, 1, 1)])
def test_original_majority_label_rule(k, majority, any_):
    """Original FLEET-GUARD _window_label: majority of frame labels, tie (50/100) -> malicious."""
    m = np.zeros(WINDOW, dtype=int)
    m[:k] = 1
    assert int(m.sum() * 2 >= WINDOW) == majority
    assert int(m.sum() > 0) == any_


release = pytest.mark.skipif(not (RELEASE / "data" / "descriptors").exists(), reason="release not exported")


@release
def test_release_descriptor_timestamps_match_observations():
    """Every exported window: descriptor timestamp = timestamp of its last frame; per-vehicle strictly increasing."""
    for sdir in sorted((RELEASE / "data" / "descriptors").iterdir())[::40]:      # every 40th session (21 sessions)
        d = pq.read_table(sdir / "descriptors.parquet").to_pandas()
        o = pq.read_table(RELEASE / "data" / "raw_parquet" / sdir.name / "can_frames.parquet",
                          columns=["frame_index", "timestamp_ns"]).to_pandas().set_index("frame_index")
        assert (o.loc[d.last_frame_index, "timestamp_ns"].values == d.descriptor_timestamp_ns.values).all()
        assert d.groupby("vehicle_id").descriptor_timestamp_ns.apply(lambda s: (np.diff(s.values) > 0).all()).all()


@release
def test_release_window_labels_follow_original_rule():
    for sdir in sorted((RELEASE / "data" / "ground_truth").iterdir())[::40]:
        lab = pq.read_table(sdir / "window_labels.parquet").to_pandas()
        assert ((lab.malicious_frame_count * 2 >= WINDOW).astype(int) == lab.label_majority_original).all()
        assert ((lab.malicious_frame_count > 0).astype(int) == lab.label_any_malicious).all()
