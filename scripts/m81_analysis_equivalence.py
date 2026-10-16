"""Milestone 8.1: prove the bounded group-wise analysis (1.3.0) equals the stored analysis outputs, which were
produced by the unchanged whole-session analysis code (descriptor 1.0). Writes only to a temporary directory.

Usage: .venv/bin/python scripts/m81_analysis_equivalence.py [--all-frozen] [--group-sizes 5,1] SID...
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"


def same_table(a: Path, b: Path) -> str:
    from src import storage
    if not storage.exists(a) and not storage.exists(b):
        return "both absent"
    if storage.exists(a) != storage.exists(b):
        return "DIFFERENT (presence)"
    fa, fb = storage.table_file(a), storage.table_file(b)
    da, db = (pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f, keep_default_na=False) for f in (fa, fb))
    if list(da.columns) != list(db.columns) or list(da.dtypes.astype(str)) != list(db.dtypes.astype(str)):
        return "DIFFERENT (schema)"
    return f"IDENTICAL ({len(da)} rows)" if da.equals(db) else "DIFFERENT (values)"


def one(args):
    sid, sizes = args
    from analysis.run_session import analyse
    res = {"session_id": sid}
    stored = DATA / "analysis" / sid
    for g in sizes:
        tmp = Path(tempfile.mkdtemp(prefix="m81an_"))
        try:
            analyse(sid, DATA, out_root=tmp, group_size=g)
            new = tmp / sid
            r = {t: same_table(stored / t, new / t) for t in ("descriptors", "descriptor_labels", "temporal_alignment")}
            vo, vn = (json.loads((d / "descriptor_validation.json").read_text()) for d in (stored, new))
            r["descriptor_validation"] = "IDENTICAL" if vo == vn else "DIFFERENT"
            res[f"g{g}"] = r
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*")
    ap.add_argument("--all-frozen", action="store_true")
    ap.add_argument("--group-sizes", default="5")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="results/m8_1_memory_scalability/analysis_equivalence.jsonl")
    a = ap.parse_args()
    sids = list(a.sessions)
    if a.all_frozen:
        sids += [r["session_id"] for r in json.loads((ROOT / "config/final/matrix/matrix_spec.json").read_text())["runs"]]
    sizes = [int(x) for x in a.group_sizes.split(",")]
    out = ROOT / a.out
    bad = 0
    with ProcessPoolExecutor(a.workers) as ex, open(out, "a") as fh:
        for res in ex.map(one, [(s, sizes) for s in sids]):
            ok = all(v.startswith(("IDENTICAL", "both absent")) for g in sizes for v in res[f"g{g}"].values())
            bad += not ok
            fh.write(json.dumps(res) + "\n")
            if not ok:
                print(json.dumps(res), flush=True)
    print(f"{len(sids)} sessions, group sizes {sizes}: {len(sids) - bad} identical, {bad} different")
