"""Verify a tar stream (stdin) of fleetcan-sim-dataset-1.0 without extracting it to disk.

Every regular member under fleetcan-sim-dataset-1.0/ is content-hashed and compared with the dataset SHA256SUMS
(the committed copy results/datasets/fleetcan-sim-dataset-1.0/SHA256SUMS, whose digest is in reference.json) and
SHA256SUMS itself. Fails on any mismatch, missing, extra or non-file member, or unless there are 835 session logs in
CSV/ and 835 in Parquet/.

Usage: zstd -dc PARTS... | python3 scripts/verify_dataset_archive_stream.py [--report FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path

NAME = "fleetcan-sim-dataset-1.0"
REPO = Path(__file__).resolve().parent.parent
REF = REPO / "results" / "datasets" / NAME
SESSIONS = 835


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report")
    a = ap.parse_args()
    info = json.loads((REF / "reference.json").read_text())
    sums = (REF / "SHA256SUMS").read_bytes()
    if hashlib.sha256(sums).hexdigest() != info["SHA256SUMS_sha256"]:
        sys.exit("STOP: committed SHA256SUMS differs from reference.json")
    expected = {}
    for line in sums.decode().splitlines():
        h, p = line.split("  ", 1)
        expected[p] = h
    expected["SHA256SUMS"] = info["SHA256SUMS_sha256"]
    prefix = f"{NAME}/"
    seen, bad, extra, nonfile, nbytes = set(), [], [], [], 0
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as tf:
        for m in tf:
            if m.isdir():
                continue
            if not m.isfile():
                nonfile.append(m.name)
                continue
            if not m.name.startswith(prefix):
                extra.append(m.name)
                continue
            rel = m.name[len(prefix):]
            h = hashlib.sha256()
            f = tf.extractfile(m)
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
            nbytes += m.size
            if rel not in expected:
                extra.append(rel)
            elif h.hexdigest() != expected[rel]:
                bad.append(rel)
            seen.add(rel)
            if len(seen) % 5000 == 0:
                print(f"  {len(seen)} files verified", file=sys.stderr, flush=True)
    missing = sorted(set(expected) - seen)
    csv_logs = sum(1 for p in seen if p.startswith("CSV/") and p.endswith(".csv.gz"))
    pq_logs = sum(1 for p in seen if p.startswith("Parquet/") and p.endswith(".parquet"))
    sessions = csv_logs if csv_logs == pq_logs else -1
    res = {"members_verified": len(seen), "expected": len(expected), "content_bytes": nbytes,
           "hash_mismatches": bad[:10], "missing": missing[:10], "extra": extra[:10], "non_file_members": nonfile[:10],
           "sessions": sessions}
    res["verdict"] = "PASS" if not (bad or missing or extra or nonfile) and sessions == SESSIONS else "FAIL"
    out = json.dumps(res, indent=1)
    print(out)
    if a.report:
        Path(a.report).write_text(out + "\n")
    sys.exit(0 if res["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
