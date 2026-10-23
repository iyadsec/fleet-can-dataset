"""Shared constants, paths and I/O helpers for the fleetcan-sim-research-1.0 derived release.

The source dataset fleetcan-sim-scale-1.0 is read-only input. Every output is written below RELEASE (or WORK for
intermediate per-session statistics) with atomic replace, so an interrupted run never leaves a partial file under its
final name and a re-run resumes from the per-session DONE markers.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent                  # repository root
SRC = Path(os.environ.get("FLEETCAN_SRC", ROOT / "data"))      # frozen source data root
SRC_FINAL = SRC / "final" / "fleetcan-sim-scale-1.0"
BASE = Path(os.environ.get("FLEETCAN_RESEARCH_BASE", Path.home() / "fleetcan_research"))
RELEASE = BASE / "fleetcan-sim-research-1.0"
WORK = BASE / "work"

SOURCE_VERSION = "fleetcan-sim-scale-1.0"
RELEASE_VERSION = "fleetcan-sim-research-1.0"
EXPORT_VERSION = "1.0.0"
MATRIX_SHA256 = "9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735"
SOURCE_SHA256SUMS = "5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70"
SOURCE_MANIFEST_SHA256 = "e5138551005b82b4370780e47efbec5792b770d34bcc61db47955a068de012c6"
EXPECTED_SESSIONS, EXPECTED_CONFIGS, EXPECTED_FRAMES = 835, 167, 444_333_222

WINDOW, STRIDE = 100, 50          # original FLEET-GUARD windowing (analysis/descriptor_schema.py)
NS = 1_000_000_000

# Detector-facing observation schema (the only columns in data/raw_parquet and data/raw_csv).
OBS_SCHEMA = pa.schema([
    pa.field("frame_index", pa.int64()),       # 0-based source row ordinal; join key to ground truth, NOT a feature
    pa.field("timestamp_ns", pa.int64()),      # observable bus time = source tx_end_ns (end of frame on the bus)
    pa.field("vehicle_id", pa.string()),       # grouping key, NOT a feature
    pa.field("can_id", pa.string()),           # source text, '0x' + 3 upper-case hex digits (11-bit)
    pa.field("dlc", pa.int8()),
    pa.field("data", pa.string()),             # source payload text, 2*dlc upper-case hex digits ('' when dlc = 0)
])
OBS_COLUMNS = OBS_SCHEMA.names

# Non-observable per-frame simulator/bus fields kept for lossless reconstruction (metadata, never detector input).
BUS_TIMING_SCHEMA = pa.schema([
    pa.field("frame_index", pa.int64()),
    pa.field("sim_time_ns", pa.int64()),       # scheduled generation time on the SUMO timeline
    pa.field("tx_start_ns", pa.int64()),       # modelled start of transmission (after arbitration/queueing)
    pa.field("frame_bits", pa.int64()),
    pa.field("capture_time_ns", pa.int64()),   # host wall-clock vCAN receive time (leakage-prone)
    pa.field("channel", pa.string()),
])


def load_manifest() -> list[dict]:
    return [json.loads(x) for x in (SRC_FINAL / "manifest.jsonl").read_text().splitlines() if x.strip()]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tmp(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


def atomic_parquet(table: pa.Table, path: Path) -> Path:
    """zstd Parquet; integer columns use DELTA_BINARY_PACKED (lossless; ~40 % smaller for monotone timestamps)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    ints = [f.name for f in table.schema if pa.types.is_integer(f.type)]
    pq.write_table(table, tmp, compression="zstd", compression_level=9, row_group_size=1 << 20,
                   use_dictionary=[c for c in table.schema.names if c not in ints],
                   column_encoding={c: "DELTA_BINARY_PACKED" for c in ints})
    os.replace(tmp, path)
    return path


def atomic_csv(table: pa.Table, path: Path) -> Path:
    """Plain CSV, or gzip-compressed CSV when the name ends with .gz. Strings quoted only when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    opts = pacsv.WriteOptions(quoting_style="needed")
    if path.suffix == ".gz":
        with pa.CompressedOutputStream(str(tmp), "gzip") as out:
            pacsv.write_csv(table, out, opts)
    else:
        pacsv.write_csv(table, str(tmp), opts)
    os.replace(tmp, path)
    return path


def atomic_text(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    tmp.write_text(text)
    os.replace(tmp, path)
    return path


def atomic_json(obj, path: Path) -> Path:
    return atomic_text(json.dumps(obj, indent=1, sort_keys=True, default=_jsonable) + "\n", path)


def _jsonable(x):
    import numpy as np
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    raise TypeError(type(x))


def read_csv_strict(path: Path, schema: pa.Schema) -> pa.Table:
    """Read a release CSV back with the declared types (strings stay strings, '' is not null)."""
    conv = pacsv.ConvertOptions(column_types={f.name: f.type for f in schema}, strings_can_be_null=False,
                                quoted_strings_can_be_null=False, null_values=[])
    return pacsv.read_csv(str(path), convert_options=conv).select(schema.names)
