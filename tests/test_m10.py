"""Milestone 10 runner: authorisation, deterministic order, pilot selection, per-session configuration (no generation)."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import run_m10_generation as R  # noqa: E402

pytestmark = pytest.mark.skipif(not R.MATRIX.exists(), reason="frozen matrix missing")


def test_wrong_sha_is_refused():
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_m10_generation.py"), "--authorize-matrix-sha", "0" * 64, "--status"],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode != 0 and "SHA mismatch" in (p.stdout + p.stderr)
    with pytest.raises(R.Stop):
        R.load_matrix("0" * 64)


def test_order_and_pilot():
    doc, sha = R.load_matrix(R.EXPECTED_SHA)
    assert sha == R.EXPECTED_SHA and doc["approved_for_generation"] is False          # frozen bytes unchanged
    ss, cfg = R.ordered_sessions(doc)
    phases = [str(cfg[s["config_id"]]["phase"]) for s in ss]
    assert len(ss) == 835 and phases == sorted(phases, key=lambda p: {"1": 0, "2": 1, "G0": 2}[p])
    assert [phases.count(p) for p in ("1", "2", "G0")] == [420, 390, 25]
    pil = R.pilot(doc)
    assert len(pil) == len(set(pil)) == 9
    cs = [cfg[next(s for s in ss if s["session_id"] == x)["config_id"]] for x in pil]
    assert {c["N"] for c in cs} >= {20, 50, 100} and {c["scenario"] for c in cs} >= {"S0", "S1", "S2", "S3", "S3-mixed"}
    assert any(c["attack"] == "replay" for c in cs) and any(c["K"] >= 50 for c in cs) and any(c["phase"] == "G0" for c in cs)
    assert any(c["temporal_pattern"] in ("partial", "bounded_spread_10s") for c in cs)


def test_session_configuration_matches_matrix(tmp_path, monkeypatch):
    from src.config import load_experiment
    doc, sha = R.load_matrix(R.EXPECTED_SHA)
    ss, cfg = R.ordered_sessions(doc)
    monkeypatch.setattr(R, "CFGS", tmp_path)
    for sid in R.pilot(doc):
        s = next(x for x in ss if x["session_id"] == sid)
        c = cfg[s["config_id"]]
        p = R.write_configs(s, c, doc, sha)
        e = load_experiment(p)
        assert e.seed == s["seed"] and e.dataset_version == R.COMPONENT and len(e.fleet) == c["N"] and e.output_root == R.STAGE
        assert e.experiment_id == sid and e.tags["matrix_sha256"] == sha and e.tags["config_id"] == c["config_id"]
        assert yaml.safe_load((p.parent / "campaigns.yaml").read_text())["campaigns"] == s["campaigns"]
        assert yaml.safe_load((p.parent / "attacks.yaml").read_text())["attacks"] == s["isolated_attacks"]
        assert p.read_bytes() == R.write_configs(s, c, doc, sha).read_bytes()         # deterministic
