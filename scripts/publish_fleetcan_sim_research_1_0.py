"""Package fleetcan-sim-research-1.0 into split tar + zstd parts and publish them as GitHub release assets, with
bounded disk use (one 1.5 GiB part on disk at a time). Run on a machine with an authenticated `gh`.

  build   SRC  : tar (pax, sorted file list, numeric owner 0) | zstd -3 | split into 1.5 GiB parts; every part is
                 hashed, uploaded to the (draft) release, its remote size checked, then deleted locally. The same
                 compressed stream is decompressed and content-verified in parallel against the committed
                 SHA256SUMS (scripts/verify_research_archive_stream.py). Writes and uploads the archive SHA256SUMS,
                 release.json, the release SHA256SUMS and README.
  verify       : downloads the parts again one by one, checks each against the archive SHA256SUMS, streams them
                 into zstd -dc | verify_research_archive_stream.py, deletes each part. Full end-to-end check.

Usage: python3 scripts/publish_fleetcan_sim_research_1_0.py build ~/Downloads/fleetcan-sim-research-1.0 --work DIR
       python3 scripts/publish_fleetcan_sim_research_1_0.py verify --work DIR
The source directory is only read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

NAME = "fleetcan-sim-research-1.0"
TAG = f"dataset-{NAME}"
REPO_SLUG = "iyadsec/fleet-can-dataset"
PART_BYTES = 1610612736          # 1.5 GiB (GitHub release assets must be < 2 GiB)
CHUNK = 8 << 20
HERE = Path(__file__).resolve().parent.parent
REF = HERE / "results" / "datasets" / NAME
REL = HERE / "results" / "dataset_release" / NAME
VERIFY = [sys.executable, str(HERE / "scripts" / "verify_research_archive_stream.py")]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def gh(*args, capture=False):
    r = subprocess.run(["gh", *args], check=True, capture_output=capture, text=True)
    return r.stdout if capture else None


def remote_sizes() -> dict:
    out = gh("release", "view", TAG, "-R", REPO_SLUG, "--json", "assets", "--jq", ".assets[] | \"\\(.name) \\(.size)\"", capture=True)
    return {n: int(s) for n, s in (l.rsplit(" ", 1) for l in out.splitlines() if l.strip())}


def upload(path: Path):
    for attempt in range(1, 6):
        try:
            gh("release", "upload", TAG, str(path), "-R", REPO_SLUG, "--clobber")
            if remote_sizes().get(path.name) == path.stat().st_size:
                return
            log(f"size mismatch after upload of {path.name}, retry {attempt}")
        except subprocess.CalledProcessError as e:
            log(f"upload of {path.name} failed ({e}), retry {attempt}")
        time.sleep(20 * attempt)
    sys.exit(f"STOP: could not upload {path.name}")


def build(src: Path, work: Path):
    ref = json.loads((REF / "reference.json").read_text())
    for f, h in [("SHA256SUMS", ref["SHA256SUMS_sha256"]), *ref["unlisted_files_sha256"].items()]:
        if hashlib.sha256((src / f).read_bytes()).hexdigest() != h:
            sys.exit(f"STOP: {src / f} differs from the committed reference")
    work.mkdir(parents=True, exist_ok=True)
    files = sorted(str(p.relative_to(src.parent)) for p in src.rglob("*") if p.is_file())
    if len(files) != ref["files"]:
        sys.exit(f"STOP: {len(files)} files in {src}, expected {ref['files']}")
    lst = work / "filelist.txt"
    lst.write_text("\n".join(files) + "\n")
    env = dict(os.environ, COPYFILE_DISABLE="1", LC_ALL="C")
    tar = subprocess.Popen(["tar", "-C", str(src.parent), "-n", "-T", str(lst), "--format", "pax", "--uid", "0",
                            "--gid", "0", "--numeric-owner", "--no-mac-metadata", "--no-xattrs", "--no-acls", "-cf", "-"],
                           stdout=subprocess.PIPE, env=env)
    zst = subprocess.Popen(["zstd", "-3", "-T4", "-q", "-c"], stdin=tar.stdout, stdout=subprocess.PIPE)
    tar.stdout.close()
    unz = subprocess.Popen(["zstd", "-dc", "-q"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    ver = subprocess.Popen(VERIFY + ["--report", str(work / "build_stream_verify.json")], stdin=unz.stdout)
    unz.stdout.close()
    parts, idx = [], 0
    done = False
    while not done:
        idx += 1
        p = work / f"{NAME}.tar.zst.part{idx:03d}"
        h, n = hashlib.sha256(), 0
        with open(p, "wb") as out:
            while n < PART_BYTES:
                buf = zst.stdout.read(min(CHUNK, PART_BYTES - n))
                if not buf:
                    done = True
                    break
                out.write(buf)
                unz.stdin.write(buf)
                h.update(buf)
                n += len(buf)
        if n == 0:
            p.unlink()
            break
        log(f"part {idx:03d}: {n} bytes, uploading")
        upload(p)
        parts.append({"file": p.name, "bytes": n, "sha256": h.hexdigest()})
        p.unlink()
        log(f"part {idx:03d} uploaded and verified remotely ({n} bytes)")
    unz.stdin.close()
    rc = (tar.wait(), zst.wait(), unz.wait(), ver.wait())
    if rc != (0, 0, 0, 0):
        sys.exit(f"STOP: pipeline exit codes {rc} (tar, zstd, unzstd, verifier)")
    sums = "".join(f"{x['sha256']}  {x['file']}\n" for x in parts)
    (work / f"{NAME}-archive-SHA256SUMS").write_text(sums)
    rel = {"dataset": NAME, "release_tag": TAG, "repository": REPO_SLUG,
           "description": "derived research release of fleetcan-sim-scale-1.0: detector-safe CAN observations (Parquet + gzip CSV), "
                          "separate ground truth and metadata, FLEET-GUARD 24-D descriptors, dataset-wide results, validation, reports",
           "sessions": 835, "frames": 444333222, "files": ref["files"], "bytes": ref["bytes"],
           "release_SHA256SUMS_sha256": ref["SHA256SUMS_sha256"], "unlisted_files_sha256": ref["unlisted_files_sha256"],
           "code_commit": ref["code_commit"],
           "archive": {"format": "tar (pax, byte-sorted file list, numeric owner/group 0)", "compression": "zstd -3 (lossless)",
                       "split_bytes": PART_BYTES, "root": f"{NAME}/", "part_count": len(parts),
                       "total_bytes": sum(x["bytes"] for x in parts), "parts": parts},
           "build_stream_verification": json.loads((work / "build_stream_verify.json").read_text())}
    (work / f"{NAME}-release.json").write_text(json.dumps(rel, indent=1) + "\n")
    for f, dst in ((work / f"{NAME}-archive-SHA256SUMS", None), (work / f"{NAME}-release.json", None),
                   (src / "SHA256SUMS", f"{NAME}-SHA256SUMS"), (src / "README.md", f"{NAME}-README.md")):
        p = f
        if dst:
            p = work / dst
            p.write_bytes(f.read_bytes())
        upload(p)
    log(f"build complete: {len(parts)} parts, {rel['archive']['total_bytes']} bytes")


def verify(work: Path):
    work.mkdir(parents=True, exist_ok=True)
    gh("release", "download", TAG, "-R", REPO_SLUG, "-D", str(work), "-p", f"{NAME}-archive-SHA256SUMS", "--clobber")
    got = (work / f"{NAME}-archive-SHA256SUMS").read_bytes()
    if (REL / f"{NAME}-archive-SHA256SUMS").exists() and got != (REL / f"{NAME}-archive-SHA256SUMS").read_bytes():
        sys.exit("STOP: release archive SHA256SUMS differs from the committed copy")
    unz = subprocess.Popen(["zstd", "-dc", "-q"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    ver = subprocess.Popen(VERIFY + ["--report", str(work / "redownload_stream_verify.json")], stdin=unz.stdout)
    unz.stdout.close()
    for line in got.decode().splitlines():
        h, fn = line.split()
        gh("release", "download", TAG, "-R", REPO_SLUG, "-D", str(work), "-p", fn, "--clobber")
        p = work / fn
        d = hashlib.sha256()
        with open(p, "rb") as fh:
            for buf in iter(lambda: fh.read(CHUNK), b""):
                d.update(buf)
                unz.stdin.write(buf)
        if d.hexdigest() != h:
            sys.exit(f"STOP: {fn} SHA-256 mismatch after download")
        p.unlink()
        log(f"{fn}: downloaded, SHA-256 OK, streamed")
    unz.stdin.close()
    rc = (unz.wait(), ver.wait())
    if rc != (0, 0):
        sys.exit(f"STOP: re-download verification failed {rc}")
    log("re-download verification PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["build", "verify"])
    ap.add_argument("src", nargs="?")
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    if a.mode == "build":
        build(Path(a.src).expanduser().resolve(), Path(a.work).expanduser())
    else:
        verify(Path(a.work).expanduser())


if __name__ == "__main__":
    main()
