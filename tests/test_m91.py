"""Milestone 9.1: frozen matrix fleetcan-sim-scale-1.0 (matrix v1) — validator, negative tests, determinism.
Nothing here generates CAN data."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import m91_freeze as F  # noqa: E402
import session_state  # noqa: E402,F401

pytestmark = pytest.mark.skipif(not F.MATRIX.exists(), reason="frozen matrix not built")
DOC = yaml.load(F.MATRIX.read_bytes(), Loader=yaml.CSafeLoader) if F.MATRIX.exists() else None


def run(path, tmp_path, *extra):
    rep = tmp_path / "rep.json"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_m91_matrix.py"), "--matrix", str(path), "--report", str(rep),
                        *extra], cwd=ROOT, capture_output=True, text=True)
    return p.returncode, json.loads(rep.read_text())


def mutate(tmp_path, fn):
    doc = copy.deepcopy(DOC)
    fn(doc)
    out = tmp_path / "m.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False))
    return out


def test_frozen_matrix_passes_everything(tmp_path):
    rc, rep = run(F.MATRIX, tmp_path)
    assert rc == 0, {k: v for k, v in rep["checks"].items() if v["status"] != "PASS"}
    assert rep["configurations"] == 167 and rep["sessions"] == 835 and rep["approved_for_generation"] is False
    assert rep["checks"]["C12_scenario_and_preflight"]["detail"]["preflight_sessions"] == 835


def test_rebuild_is_byte_identical():
    y, c = F.build()
    assert y == F.MATRIX.read_bytes() and c == F.SESSIONS_CSV.read_bytes()


def test_builder_refuses_to_overwrite_with_different_content(tmp_path, monkeypatch):
    fake = tmp_path / "m.yaml"
    fake.write_text("different")
    monkeypatch.setattr(F, "MATRIX", fake)
    monkeypatch.setattr(sys, "argv", ["m91_freeze.py"])
    with pytest.raises(SystemExit) as e:
        F.main()
    assert "immutable" in str(e.value) and fake.read_text() == "different"


def _cfg(doc, **kw):
    return next(c for c in doc["configurations"] if all(c.get(k) == v for k, v in kw.items()))


def _sess(doc, **kw):
    cid = _cfg(doc, **kw)["config_id"]
    return next(s for s in doc["sessions"] if s["config_id"] == cid)


def _drop_g0(d):
    cid = d["configurations"][-1]["config_id"]
    d["configurations"].pop()
    d["sessions"] = [s for s in d["sessions"] if s["config_id"] != cid]


def _add_g1(d):
    c = copy.deepcopy(_cfg(d, phase="G0", temporal_pattern="small"))
    c.update(config_id="FCS10_G0_N010_S3_fuzzy_K005_large", temporal_pattern="large", config_index=len(d["configurations"]))
    c["session_ids"] = [f"{c['config_id']}_R{i}" for i in range(1, 6)]
    d["configurations"].append(c)


def _set_campaign(d, scen, key, val, **kw):
    _sess(d, scenario=scen, **kw)["campaigns"][0][key] = val


CASES = [
    ("drop_G0_config", _drop_g0, ["C01_167_configurations", "C02_835_sessions", "C04_G0_exact"]),
    ("add_G1", _add_g1, ["C01_167_configurations", "C05_G1_absent"]),
    ("approved", lambda d: d.__setitem__("approved_for_generation", True), ["C21_not_approved"]),
    ("status", lambda d: d.__setitem__("matrix_status", "proposed"), ["C20_status_frozen"]),
    ("S2_coordinated", lambda d: _set_campaign(d, "S2", "coordinated", True), ["C18_S2_independent"]),
    ("S3_not_coordinated", lambda d: _set_campaign(d, "S3", "coordinated", False, temporal_pattern="small"), ["C19_S3_coordinated"]),
    ("bounded_delay", lambda d: _sess(d, temporal_pattern="bounded_spread_2s")["campaigns"][0]["relative_delays_s"].__setitem__(-1, 2.5),
     ["C15_bounded_spread"]),
    ("bounded_as_sequential", lambda d: _sess(d, temporal_pattern="bounded_spread_10s", K=20)["campaigns"][0].__setitem__(
        "relative_delays_s", [0.5 * i for i in range(20)]), ["C15_bounded_spread"]),
    ("partial_5s", lambda d: _sess(d, temporal_pattern="partial", attack="flooding")["campaigns"][0].__setitem__("duration_s", 5.0),
     ["C16_partial_overlap"]),
    ("flood_rate", lambda d: _sess(d, scenario="S3", attack="flooding")["campaigns"][0]["params"].__setitem__("rate_hz", 1500),
     ["C13_attack_parameters_frozen"]),
    ("truncated", lambda d: _cfg(d, temporal_pattern="large").__setitem__("duration_s", 50.0), ["C17_duration"]),
    ("dup_seed", lambda d: d["sessions"][1].__setitem__("seed", d["sessions"][0]["seed"]), ["C08_seeds"]),
    ("baseline_seed", lambda d: d["sessions"][0].__setitem__("seed", 610001), ["C08_seeds"]),
    ("baseline_session", lambda d: d["sessions"][0].__setitem__("session_id", "FC11_N02_S0_none_none_none_R1"), ["C03_repetitions"]),
    ("K_gt_N", lambda d: _cfg(d, scenario="S3", N=20, K=20).__setitem__("K", 50), ["C09_K_le_N"]),
    ("member_swap", lambda d: _sess(d, scenario="S3", N=100, K=5)["campaigns"][0]["targets"].__setitem__(0, "vehicle_100"),
     ["C10_member_feasibility"]),
    ("bad_attack", lambda d: _cfg(d, scenario="S1").__setitem__("attack", "dos"), ["C13_attack_parameters_frozen"]),
]


@pytest.mark.parametrize("name,fn,checks", CASES, ids=[c[0] for c in CASES])
def test_validator_detects_violation(tmp_path, name, fn, checks):
    rc, rep = run(mutate(tmp_path, fn), tmp_path, "--manifest", "none", "--no-preflight")
    assert rc == 1
    failed = {k for k, v in rep["checks"].items() if v["status"] == "FAIL"}
    assert set(checks) <= failed, (name, failed)
    assert "C23_identity" in failed                        # any change breaks the frozen identity


def test_g0_is_baseline_except_route_source():
    g0 = [c for c in DOC["configurations"] if c["phase"] == "G0"]
    assert len(g0) == 5 and all(c["fleet_config"] == "config/final/fleets/fleet_10.yaml" for c in g0)
    for c in g0:
        if c["scenario"] == "S3":
            base = yaml.safe_load((ROOT / "config" / "final" / "matrix" / f"N10_S3_{c['attack']}_s05_{c['temporal_pattern']}" /
                                   "campaigns.yaml").read_text())["campaigns"][0]
            for s in (s for s in DOC["sessions"] if s["config_id"] == c["config_id"]):
                got = s["campaigns"][0]
                assert got["targets"] == base["targets"] and got["start_s"] == base["start_s"] and got["params"] == base["params"]
                assert [float(x) for x in got.get("relative_delays_s", [0] * 5)] == [float(x) for x in base.get("relative_delays_s", [0] * 5)]
                assert got["campaign_id"] == base["campaign_id"] and got["coordinated"] is True


def test_seed_namespace_and_counts():
    seeds = [s["seed"] for s in DOC["sessions"]]
    assert len(set(seeds)) == 835 and min(seeds) == 920001 and max(seeds) == 921665
    assert DOC["counts"]["phases"] == {"1": {"configurations": 84, "sessions": 420}, "2": {"configurations": 78, "sessions": 390},
                                       "G0": {"configurations": 5, "sessions": 25}}


def _mk(root, sid, dirs, validation=None, analysis=None, tags=None):
    for d in dirs:
        (root / d / sid).mkdir(parents=True, exist_ok=True)
    if "metadata" in dirs:
        (root / "metadata" / sid / "metadata.json").write_text(json.dumps({"tags": tags or {}}))
    if validation:
        (root / "validation" / sid / "validation_report.json").write_text(json.dumps({"overall": validation}))
    if analysis:
        (root / "analysis" / sid).mkdir(parents=True, exist_ok=True)
        (root / "analysis" / sid / "descriptor_validation.json").write_text(json.dumps({"overall": analysis}))


def test_session_state_classification_and_clear_partial(tmp_path):
    from session_state import clear_partial, session_state
    allv = ("raw", "ground_truth", "validation", "metadata")
    assert session_state("X0", tmp_path)["state"] == "absent"
    (tmp_path / ".incomplete" / "X1").mkdir(parents=True)
    assert session_state("X1", tmp_path)["state"] == "staged_incomplete"
    _mk(tmp_path, "X2", ("raw", "ground_truth"))
    assert session_state("X2", tmp_path)["state"] == "partial_published"
    _mk(tmp_path, "X3", allv, validation="FAIL")
    assert session_state("X3", tmp_path)["state"] == "generated_validation_failed"
    _mk(tmp_path, "X4", allv, validation="PASS")
    assert session_state("X4", tmp_path)["state"] == "analysis_pending"
    _mk(tmp_path, "X5", allv, validation="PASS", analysis="FAIL")
    assert session_state("X5", tmp_path)["state"] == "analysis_failed"
    _mk(tmp_path, "X6", allv, validation="PASS", analysis="PASS", tags={"matrix_sha256": "a"})
    assert session_state("X6", tmp_path, {"matrix_sha256": "a"})["state"] == "complete"
    assert session_state("X6", tmp_path, {"matrix_sha256": "b"})["state"] == "provenance_mismatch"
    assert set(clear_partial("X2", tmp_path)) == {"raw/X2", "ground_truth/X2"} and session_state("X2", tmp_path)["state"] == "absent"
    assert clear_partial("X1", tmp_path) == [".incomplete/X1"]
    for sid in ("X3", "X4", "X6"):
        with pytest.raises(SystemExit):
            clear_partial(sid, tmp_path)
    _mk(tmp_path, "FC11_N02_S0_none_none_none_R1", ("raw",))
    with pytest.raises(SystemExit):
        clear_partial("FC11_N02_S0_none_none_none_R1", tmp_path)
