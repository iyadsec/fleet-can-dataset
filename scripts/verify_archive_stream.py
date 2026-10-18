"""Milestone 11: verify the split archive of fleetcan-sim-scale-1.0 using ONLY the archive parts (no extraction to disk).

1 every part's SHA-256 against <name>-archive-SHA256SUMS
2 the parts are concatenated in order, decompressed with zstd and read as a tar STREAM; for every member the
  SHA-256 of its content is computed and compared with the authoritative dataset SHA256SUMS (the committed copy
  results/datasets/fleetcan-sim-scale-1.0/SHA256SUMS, whose digest must be the frozen value)
3 exactly the expected files, no missing, no extra, no non-file members, paths under fleetcan-sim-scale-1.0/data/,
  SHA256SUMS and FREEZE.json inside the archive equal the committed copies, 835 sessions

Usage: python3 scripts/verify_archive_stream.py ARCHIVE_DIR
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

NAME = "fleetcan-sim-scale-1.0"
SUMS_SHA = "5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70"
REPO = Path(__file__).resolve().parent.parent
REF = REPO / "results" / "datasets" / NAME


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    d = Path(sys.argv[1])
    if sha(REF / "SHA256SUMS") != SUMS_SHA:
        sys.exit("STOP: committed SHA256SUMS copy differs from the frozen digest")
    expected = {}
    for line in (REF / "SHA256SUMS").read_text().splitlines():
        h, p = line.split("  ", 1)
        expected[p] = h
    expected[f"final/{NAME}/SHA256SUMS"] = SUMS_SHA
    expected[f"final/{NAME}/FREEZE.json"] = sha(REF / "FREEZE.json")
    parts = []
    for line in (d / f"{NAME}-archive-SHA256SUMS").read_text().splitlines():
        h, fn = line.split()
        got = sha(d / fn)
        if got != h:
            sys.exit(f"STOP: part {fn} SHA-256 mismatch")
        parts.append(d / fn)
    print(f"{len(parts)} parts: SHA-256 OK", flush=True)
    cat = subprocess.Popen(["cat", *map(str, parts)], stdout=subprocess.PIPE)
    unz = subprocess.Popen(["zstd", "-dc", "-q"], stdin=cat.stdout, stdout=subprocess.PIPE)
    cat.stdout.close()
    prefix = f"{NAME}/data/"
    seen, bad, extra, nonfile, nbytes = set(), [], [], [], 0
    with tarfile.open(fileobj=unz.stdout, mode="r|") as tf:
        for m in tf:
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
                print(f"  {len(seen)} files verified", flush=True)
    rc = unz.wait(), cat.wait()
    missing = sorted(set(expected) - seen)
    sessions = len({p.split("/")[1] for p in seen if p.startswith("metadata/FCS10_") and p.endswith("/metadata.json")})
    res = {"parts": len(parts), "members_verified": len(seen), "expected": len(expected), "content_bytes": nbytes,
           "hash_mismatches": bad[:10], "missing": missing[:10], "extra": extra[:10], "non_file_members": nonfile[:10],
           "sessions": sessions, "decompress_exit": rc}
    res["verdict"] = "PASS" if not bad and not missing and not extra and not nonfile and sessions == 835 and rc == (0, 0) else "FAIL"
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
