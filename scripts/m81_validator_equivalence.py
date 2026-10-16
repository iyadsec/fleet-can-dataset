"""Milestone 8.1: prove the bounded group-wise validator (1.3.0) equals the whole-session validator (1.2.0).

For each session: run both on the same files (read-only) and compare every check (id, status, detail) and the
informational statistics. Usage: .venv/bin/python scripts/m81_validator_equivalence.py [--all-frozen] [--group-sizes 5,1] SID...
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"


def norm(rep):
    return json.loads(json.dumps({"overall": rep.overall, "checks": rep.checks, "info": rep.info}, default=str))


def one(args):
    sid, sizes = args
    from src import validator
    from tests.reference import validator_1_2 as ref
    old = norm(ref.validate(sid, DATA))
    res = {"session_id": sid, "overall": old["overall"]}
    for g in sizes:
        new = norm(validator.validate(sid, DATA, group_size=g))
        diff = [(a["id"], a["detail"][:120], b["detail"][:120]) for a, b in zip(old["checks"], new["checks"]) if a != b]
        res[f"g{g}"] = {"checks_identical": old["checks"] == new["checks"], "info_identical": old["info"] == new["info"],
                        "diff": diff[:3], "n_checks": [len(old["checks"]), len(new["checks"])],
                        "info_keys_diff": [k for k in set(old["info"]) | set(new["info"]) if old["info"].get(k) != new["info"].get(k)]}
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*")
    ap.add_argument("--all-frozen", action="store_true")
    ap.add_argument("--group-sizes", default="5")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="results/m8_1_memory_scalability/validator_equivalence.jsonl")
    a = ap.parse_args()
    sids = list(a.sessions)
    if a.all_frozen:
        sids += [r["session_id"] for r in json.loads((ROOT / "config/final/matrix/matrix_spec.json").read_text())["runs"]]
    sizes = [int(x) for x in a.group_sizes.split(",")]
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    bad = 0
    with ProcessPoolExecutor(a.workers) as ex, open(out, "a") as fh:
        for res in ex.map(one, [(s, sizes) for s in sids]):
            ok = all(res[f"g{g}"]["checks_identical"] and res[f"g{g}"]["info_identical"] for g in sizes)
            bad += not ok
            fh.write(json.dumps(res) + "\n")
            if not ok:
                print(json.dumps(res), flush=True)
    print(f"{len(sids)} sessions, group sizes {sizes}: {len(sids) - bad} identical, {bad} different")
