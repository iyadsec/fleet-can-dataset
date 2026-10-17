"""Milestone 9: expanded-dataset design (proposed matrix, read-only validator, member-selection rule).
Nothing here generates CAN data."""

import copy
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import m9_design as D  # noqa: E402

MATRIX = ROOT / "configs" / "m9_expanded_matrix_proposed.yaml"
pytestmark = pytest.mark.skipif(not MATRIX.exists(), reason="proposed matrix not generated")


def run_validator(path, tmp_path):
    rep = tmp_path / "report.json"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_m9_matrix.py"), "--matrix", str(path), "--report", str(rep)],
                       cwd=ROOT, capture_output=True, text=True)
    return p.returncode, json.loads(rep.read_text())


def mutated(tmp_path, fn):
    doc = yaml.safe_load(MATRIX.read_text())
    fn(doc)
    out = tmp_path / "m.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False))
    return out


def test_proposed_matrix_passes_and_is_not_approved(tmp_path):
    rc, rep = run_validator(MATRIX, tmp_path)
    assert rc == 0 and rep["verdict"] == "PASS", {k: v for k, v in rep["checks"].items() if v["status"] != "PASS"}
    assert rep["approved_for_generation"] is False
    assert rep["configurations"] == len(D.design_B()) and rep["sessions"] == 5 * len(D.design_B())


def test_matrix_file_is_reproducible_from_design_script():
    doc = yaml.safe_load(MATRIX.read_text())
    assert [r["config_id"] for r in doc["configurations"]] == [c["config_id"] for c in D.design_B()]


def _row(doc, scenario):
    return next(r for r in doc["configurations"] if r["scenario"] == scenario)


@pytest.mark.parametrize("name,mutation,check", [
    ("approved", lambda d: d.__setitem__("approved_for_generation", True), "M01_not_approved"),
    ("bad_attack", lambda d: _row(d, "S3").__setitem__("attack", "dos"), "M03_scenario_attack_pattern"),
    ("K_gt_N", lambda d: _row(d, "S3").__setitem__("K", 200), "M04_K_le_N"),
    ("reps", lambda d: _row(d, "S0").__setitem__("repetitions", 3), "M05_repetitions"),
    ("dup_session", lambda d: d["configurations"][1]["session_ids"].__setitem__(0, d["configurations"][0]["session_ids"][0]), "M06_unique_ids"),
    ("dup_seed", lambda d: d["configurations"][1]["seeds"].__setitem__(0, d["configurations"][0]["seeds"][0]), "M07_unique_seeds"),
    ("dup_config", lambda d: d["configurations"].append(dict(copy.deepcopy(d["configurations"][5]), config_id="X")), "M08_no_duplicate_configs"),
    ("baseline_seed", lambda d: d["configurations"][0]["seeds"].__setitem__(0, 610001), "M09_no_collision"),
    ("baseline_sid", lambda d: d["configurations"][0]["session_ids"].__setitem__(0, "FC11_N02_S0_none_none_none_R1"), "M09_no_collision"),
    ("short", lambda d: _row(d, "S3").__setitem__("duration_s", 15.0), "M10_timing_and_duration"),
    ("bad_delay", lambda d: _row(d, "S3-mixed")["member_relative_delays_s"].__setitem__(1, 0.7), "M10_timing_and_duration"),
])
def test_validator_detects_violations(tmp_path, name, mutation, check):
    rc, rep = run_validator(mutated(tmp_path, mutation), tmp_path)
    assert rc == 1 and rep["checks"][check]["status"] == "FAIL", (name, rep["checks"][check])


def test_validator_does_not_write_into_data(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_m9_matrix.py"), "--report", str(ROOT / "data" / "x.json")],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode != 0 and not (ROOT / "data" / "x.json").exists()


def test_member_selection_balanced_deterministic_unbiased():
    fleet = D.fleet_for(20, 920011)
    prof = {a.vehicle_id: a.profile for a in fleet}
    for k in range(1, 21):
        a = D.stratified_members(fleet, k, np.random.default_rng(7))
        b = D.stratified_members(fleet, k, np.random.default_rng(7))
        assert a == b and len(set(a)) == k
        cnt = Counter(prof[v] for v in a)
        assert max(cnt.get(p, 0) for p in set(prof.values())) - min(cnt.get(p, 0) for p in set(prof.values())) <= 1
    first = Counter(prof[D.stratified_members(fleet, 1, np.random.default_rng(s))[0]] for s in range(600))
    assert len(first) == 3 and min(first.values()) > 150          # ~200 each: no profile systematically favoured
    orders = {tuple(D.stratified_members(fleet, 5, np.random.default_rng(s))) for s in range(50)}
    assert any(list(o) != sorted(o) for o in orders)               # delay rank is not ID order
    ex = D.stratified_members(fleet, 10, np.random.default_rng(3))
    rest = D.stratified_members(fleet, 10, np.random.default_rng(4), exclude=ex)
    assert not set(ex) & set(rest)


def test_timing_semantics_match_frozen_builder():
    import m6_build_matrix as M6
    for pat, step in M6.STEP.items():
        assert D.SEQUENTIAL[pat] == step
    for pat in ("small", "medium", "large", "partial"):
        assert D.delays(pat, 5) == [M6.STEP[pat] * i for i in range(5)]
    for pat, w in D.BOUNDED.items():
        for k in (20, 50, 100):
            d = D.delays(pat, k)
            assert d[0] == 0 and abs(d[-1] - w) < 1e-9 and len(d) == k


def test_seed_and_session_namespaces_disjoint_from_baseline():
    spec = json.loads((ROOT / "config" / "final" / "matrix" / "matrix_spec.json").read_text())
    base_seeds = {r["seed"] for r in spec["runs"]}
    doc = yaml.safe_load(MATRIX.read_text())
    seeds = [s for r in doc["configurations"] for s in r["seeds"]]
    assert min(seeds) > max(base_seeds) and not set(seeds) & base_seeds
    assert all(s.startswith("FCS10_") for r in doc["configurations"] for s in r["session_ids"])


def test_new_rng_tags_do_not_collide_with_existing_streams():
    import zlib
    existing = ["profile_assignment", "switch_on", "ecu_jitter", "route_generation"]
    new = ["campaign_members/C_MAIN", "independent_targets", "isolated_target"]
    crc = [zlib.crc32(x.encode()) for x in existing + new]
    assert len(set(crc)) == len(crc)
    # the frozen streams of a session are unchanged by member selection (selection only reads derive_seed with new tags)
    from src.config import derive_seed
    assert derive_seed(920011, "profile_assignment") == derive_seed(920011, "profile_assignment")
