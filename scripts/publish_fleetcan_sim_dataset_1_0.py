"""Package fleetcan-sim-dataset-1.0 into split tar + zstd parts and publish them as GitHub release assets, with
bounded disk use (one 1.5 GiB part on disk at a time). Run on a machine with an authenticated `gh`.

  build   SRC  : tar (pax, sorted file list, numeric owner 0) | zstd -3 | split into 1.5 GiB parts; every part is
                 hashed, uploaded to the (draft) release, its remote size checked, then deleted locally. The same
                 compressed stream is decompressed and content-verified in parallel against the committed
                 SHA256SUMS (scripts/verify_dataset_archive_stream.py). Writes and uploads the archive SHA256SUMS,
                 release.json, the release SHA256SUMS and README.
  verify       : downloads the parts again one by one, checks each against the archive SHA256SUMS, streams them
                 into zstd -dc | verify_dataset_archive_stream.py, deletes each part. Full end-to-end check.

Usage: python3 scripts/publish_fleetcan_sim_dataset_1_0.py build SRC --work DIR
       (SRC = local dataset directory, or HOST:/path/fleetcan-sim-dataset-1.0 to stream it from that host over ssh)
       python3 scripts/publish_fleetcan_sim_dataset_1_0.py verify --work DIR
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

NAME = "fleetcan-sim-dataset-1.0"
TAG = f"dataset-{NAME}"
REPO_SLUG = "iyadsec/fleet-can-dataset"
PART_BYTES = 1610612736          # 1.5 GiB (GitHub release assets must be < 2 GiB)
CHUNK = 8 << 20
HERE = Path(__file__).resolve().parent.parent
REF = HERE / "results" / "datasets" / NAME
REL = HERE / "results" / "dataset_release" / NAME
VERIFY = [sys.executable, str(HERE / "scripts" / "verify_dataset_archive_stream.py")]


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


def tar_command(src: str, work: Path, ref: dict) -> list[str]:
    """Local directory: bsdtar/GNU tar over a sorted file list. HOST:DIR: GNU tar on that host over ssh, streamed."""
    if ":" in src and not Path(src).exists():
        host, d = src.split(":", 1)
        parent, name = d.rstrip("/").rsplit("/", 1)
        remote = (f"cd {parent} && test \"$(sha256sum {name}/SHA256SUMS | cut -c1-64)\" = {ref['SHA256SUMS_sha256']} && "
                  f"test \"$(find {name} -type f | wc -l)\" = {ref['files']} && "
                  f"tar --sort=name --format=posix --pax-option=delete=atime,delete=ctime --owner=0 --group=0 "
                  f"--numeric-owner -cf - {name}")
        return ["ssh", host, remote]
    root = Path(src).expanduser().resolve()
    if hashlib.sha256((root / "SHA256SUMS").read_bytes()).hexdigest() != ref["SHA256SUMS_sha256"]:
        sys.exit(f"STOP: {root}/SHA256SUMS differs from the committed reference")
    files = sorted(str(p.relative_to(root.parent)) for p in root.rglob("*") if p.is_file())
    if len(files) != ref["files"]:
        sys.exit(f"STOP: {len(files)} files in {root}, expected {ref['files']}")
    lst = work / "filelist.txt"
    lst.write_text("\n".join(files) + "\n")
    return ["tar", "-C", str(root.parent), "-n", "-T", str(lst), "--format", "pax", "--uid", "0", "--gid", "0",
            "--numeric-owner", "--no-mac-metadata", "--no-xattrs", "--no-acls", "-cf", "-"]


def build(src: str, work: Path):
    ref = json.loads((REF / "reference.json").read_text())
    work.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, COPYFILE_DISABLE="1", LC_ALL="C")
    tar = subprocess.Popen(tar_command(src, work, ref), stdout=subprocess.PIPE, env=env)
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
           "description": "FLEETCAN-SIM multi-vehicle CAN intrusion dataset: one labelled CAN log per session "
                          "(Timestamp, Vehicle_ID, Arbitration_ID, DLC, Data, Class, SubClass, Attack_ID, Campaign_ID) "
                          "as gzip CSV and Parquet, plus session, vehicle, attack, campaign and replay-link tables",
           "sessions": 835, "frames": 444333222, "files": ref["files"], "bytes": ref["bytes"],
           "dataset_SHA256SUMS_sha256": ref["SHA256SUMS_sha256"], "source_dataset": ref["source_dataset"],
           "archive": {"format": "tar (pax, byte-sorted file list, numeric owner/group 0)", "compression": "zstd -3 (lossless)",
                       "split_bytes": PART_BYTES, "root": f"{NAME}/", "part_count": len(parts),
                       "total_bytes": sum(x["bytes"] for x in parts), "parts": parts},
           "build_stream_verification": json.loads((work / "build_stream_verify.json").read_text())}
    (work / f"{NAME}-release.json").write_text(json.dumps(rel, indent=1) + "\n")
    (work / f"{NAME}-SHA256SUMS").write_bytes((REF / "SHA256SUMS").read_bytes())
    (work / f"{NAME}-README.md").write_bytes((REF / "README.md").read_bytes())
    for p in (work / f"{NAME}-archive-SHA256SUMS", work / f"{NAME}-release.json", work / f"{NAME}-SHA256SUMS",
              work / f"{NAME}-README.md"):
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
        build(a.src, Path(a.work).expanduser())
    else:
        verify(Path(a.work).expanduser())


if __name__ == "__main__":
    main()
