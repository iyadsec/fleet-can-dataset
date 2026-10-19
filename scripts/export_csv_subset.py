"""Export selected fleetcan-sim-scale-1.0 sessions to plain CSV, organised by the dataset's main structure.

Read-only on the frozen dataset (Parquet files are only read). For every selected session it writes two files into
  <OUT>/<scenario>/<attack>/N<NNN>/<session_id>_can_frames.csv   raw observable CAN frames (all columns of
                                                                  raw/<sid>/can_frames, same order and values)
  <OUT>/<scenario>/<attack>/N<NNN>/<session_id>_labels.csv       per-frame ground truth (ground_truth/<sid>/frame_labels)
scenario folders: S0_benign, S1_isolated, S2_independent, S3_coordinated, S3_mixed, S3_replay_probe
(N010 = G0 route-source bridge sessions). Rows are streamed per Parquet batch (bounded memory). After writing, each
CSV is re-read and every row and value is compared with the Parquet source (as text).
A manifest line (JSON) per session with metadata, row counts and SHA-256 of both CSVs is appended to
<OUT>/export_manifest.jsonl.

Usage: .venv/bin/python scripts/export_csv_subset.py --out DIR --repetition 1 [--start I --count N]
       .venv/bin/python scripts/export_csv_subset.py --out DIR --sessions SID [SID ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import run_m10_generation as R  # noqa: E402  (frozen-matrix loader with SHA check; read-only)

DATA = ROOT / "data"
SCEN = {"S0": "S0_benign", "S1": "S1_isolated", "S2": "S2_independent", "S3": "S3_coordinated",
        "S3-mixed": "S3_mixed", "S3-probe": "S3_replay_probe"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_csv(src: Path, dst: Path) -> int:
    pf = pq.ParquetFile(src)
    n = 0
    with pacsv.CSVWriter(dst, pf.schema_arrow) as w:
        for b in pf.iter_batches(batch_size=200_000):
            w.write_batch(b)
            n += b.num_rows
    # check: every row and every value of the CSV equals the Parquet source (compared as text; null -> "")
    strs = pacsv.ConvertOptions(column_types={f.name: pa.string() for f in pf.schema_arrow}, strings_can_be_null=False,
                                quoted_strings_can_be_null=False)
    back = pacsv.read_csv(dst, convert_options=strs)
    src_t = pq.read_table(src)
    if back.num_rows != pf.metadata.num_rows or back.column_names != src_t.column_names or n != back.num_rows:
        raise SystemExit(f"CSV check failed for {dst}")
    for col in src_t.column_names:
        a = pc.fill_null(src_t.column(col).cast(pa.string()), "")
        if not a.equals(back.column(col)):
            raise SystemExit(f"CSV content differs from Parquet: {dst} column {col}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--repetition", type=int)
    g.add_argument("--sessions", nargs="+")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--count", type=int, default=None)
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()
    if out.is_relative_to(DATA.resolve()):
        sys.exit("refusing to write inside data/")
    doc, msha = R.load_matrix(R.EXPECTED_SHA)
    ss, cfg = R.ordered_sessions(doc)
    sel = [s for s in ss if (s["repetition"] == a.repetition if a.repetition else s["session_id"] in a.sessions)]
    sel = sel[a.start: a.start + a.count if a.count else None]
    out.mkdir(parents=True, exist_ok=True)
    for s in sel:
        c, sid = cfg[s["config_id"]], s["session_id"]
        d = out / SCEN[c["scenario"]] / c["attack"] / f"N{c['N']:03d}"
        d.mkdir(parents=True, exist_ok=True)
        fr, lb = d / f"{sid}_can_frames.csv", d / f"{sid}_labels.csv"
        n1 = to_csv(DATA / "raw" / sid / "can_frames.parquet", fr)
        n2 = to_csv(DATA / "ground_truth" / sid / "frame_labels.parquet", lb)
        if n1 != n2:
            raise SystemExit(f"{sid}: frames {n1} != labels {n2}")
        rec = {"session_id": sid, "config_id": c["config_id"], "repetition": s["repetition"], "seed": s["seed"], "phase": str(c["phase"]),
               "purpose": c["purpose"], "N": c["N"], "K": c["K"], "scenario": c["scenario"], "attack": c["attack"],
               "timing": c["temporal_pattern"] if c["scenario"] != "S2" else c["independent_timing"],
               "independent_attack_count": c["independent_attack_count"], "mixed_attack_count": c["mixed_attack_count"],
               "duration_s": c["duration_s"], "frames": n1,
               "can_frames_csv": str(fr.relative_to(out)), "can_frames_sha256": sha256(fr), "can_frames_bytes": fr.stat().st_size,
               "labels_csv": str(lb.relative_to(out)), "labels_sha256": sha256(lb), "labels_bytes": lb.stat().st_size}
        with open(out / "export_manifest.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"{sid} {n1} frames", flush=True)


if __name__ == "__main__":
    main()
