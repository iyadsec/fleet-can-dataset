"""Release integrity: MANIFEST.csv + SHA256SUMS (build) and full verification (verify).

Usage: .venv/bin/python -m research.integrity build|verify [--workers 4]
build  : hashes every release file (except SHA256SUMS/MANIFEST.csv themselves), writes MANIFEST.csv (path, bytes,
         sha256, area, session_id, format, source mapping) and SHA256SUMS (sha256sum -c compatible).
verify : re-hashes every file against SHA256SUMS, checks 835 sessions per data area, frame totals from Parquet
         metadata (raw_parquet, frame_labels, bus timing) = 444,333,222, and writes validation/output_integrity.csv.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research.common import EXPECTED_FRAMES, EXPECTED_SESSIONS, RELEASE, atomic_csv, atomic_text, load_manifest, sha256_file  # noqa: E402

SKIP = {"SHA256SUMS", "MANIFEST.csv"}
SOURCE_MAP = {
    ("raw_parquet", "can_frames.parquet"): "raw/{sid}/can_frames.parquet (frame_index,tx_end_ns,vehicle_id,can_id,dlc,payload)",
    ("raw_csv", "can_frames.csv.gz"): "raw/{sid}/can_frames.parquet (same columns as raw_parquet)",
    ("metadata", "frame_bus_timing.parquet"): "raw/{sid}/can_frames.parquet (sim_time_ns,tx_start_ns,frame_bits,capture_time_ns,channel)",
    ("metadata", "vehicles.csv"): "metadata/{sid}/metadata.json + raw/{sid}/vehicle_states.parquet",
    ("metadata", "session.json"): "final/fleetcan-sim-scale-1.0/manifest.jsonl + metadata/{sid}/metadata.json",
    ("descriptors", "descriptors.parquet"): "derived from raw/{sid}/can_frames.parquet (observable columns)",
    ("descriptors", "descriptors.csv.gz"): "derived from raw/{sid}/can_frames.parquet (observable columns)",
}


def files() -> list[Path]:
    return sorted(p for p in RELEASE.rglob("*") if p.is_file() and p.name not in SKIP and not p.name.endswith(".tmp"))


def describe(p: Path) -> dict:
    rel = p.relative_to(RELEASE)
    parts = rel.parts
    area = parts[1] if parts[0] == "data" and len(parts) > 1 else parts[0]
    sid = parts[2] if parts[0] == "data" and len(parts) > 2 else ""
    fmt = "".join(p.suffixes[-2:]) if p.name.endswith(".gz") else p.suffix
    src = SOURCE_MAP.get((area, p.name), "")
    if not src and area == "ground_truth":
        base = p.name.split(".")[0]
        src = ("derived: ground_truth + raw observations" if base in ("attack_instances", "window_labels")
               else f"ground_truth/{{sid}}/{base}.parquet (+ frame_index join keys)")
    if not src and area == "metadata" and "source" in parts:
        src = "metadata/{sid}/ or validation/{sid}/ (verbatim copy)"
    return {"path": str(rel), "bytes": p.stat().st_size, "area": area, "session_id": sid, "format": fmt,
            "source_mapping": src.format(sid=sid) if src else ""}


def _hash(p: str) -> tuple[str, str]:
    return p, sha256_file(Path(p))


def build(workers: int):
    fl = files()
    with ProcessPoolExecutor(workers) as ex:
        digests = dict(ex.map(_hash, [str(p) for p in fl], chunksize=64))
    rows = [{**describe(p), "sha256": digests[str(p)]} for p in fl]
    df = pd.DataFrame(rows)
    atomic_csv(pa.Table.from_pandas(df, preserve_index=False), RELEASE / "MANIFEST.csv")
    atomic_text("".join(f"{r['sha256']}  {r['path']}\n" for r in rows), RELEASE / "SHA256SUMS")
    print(f"files {len(df)} bytes {int(df.bytes.sum())}")


def verify(workers: int):
    sums = [l.split("  ", 1) for l in (RELEASE / "SHA256SUMS").read_text().splitlines()]
    paths = [str(RELEASE / p) for _, p in sums]
    with ProcessPoolExecutor(workers) as ex:
        got = dict(ex.map(_hash, paths, chunksize=64))
    bad = [p for (d, p) in sums if got[str(RELEASE / p)] != d]
    on_disk = {str(p.relative_to(RELEASE)) for p in files()}
    listed = {p for _, p in sums}
    sids = sorted(r["session_id"] for r in load_manifest())
    rows = [{"check": "sha256_files_listed", "value": len(sums), "pass": True},
            {"check": "sha256_mismatches", "value": len(bad), "pass": not bad},
            {"check": "files_on_disk_not_listed", "value": len(on_disk - listed), "pass": not (on_disk - listed)},
            {"check": "listed_files_missing", "value": len(listed - on_disk), "pass": not (listed - on_disk)}]
    for area in ("raw_parquet", "raw_csv", "ground_truth", "metadata", "descriptors"):
        present = sorted(p.name for p in (RELEASE / "data" / area).iterdir() if p.is_dir())
        rows.append({"check": f"sessions_in_{area}", "value": len(present), "pass": present == sids and len(present) == EXPECTED_SESSIONS})
    for label, rel in (("frames_raw_parquet", "raw_parquet/{}/can_frames.parquet"), ("frames_frame_labels", "ground_truth/{}/frame_labels.parquet"),
                       ("frames_bus_timing", "metadata/{}/frame_bus_timing.parquet")):
        n = sum(pq.read_metadata(RELEASE / "data" / rel.format(s)).num_rows for s in sids)
        rows.append({"check": label, "value": n, "pass": n == EXPECTED_FRAMES})
    df = pd.DataFrame(rows)
    atomic_csv(pa.Table.from_pandas(df.astype({"value": str}), preserve_index=False), RELEASE / "validation" / "output_integrity.csv")
    print(df.to_string())
    print("bad examples:", bad[:5])
    return bool(df["pass"].all())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["build", "verify"])
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    if a.mode == "build":
        build(a.workers)
    else:
        sys.exit(0 if verify(a.workers) else 1)


if __name__ == "__main__":
    main()
