"""Table storage backend: CSV (debugging, inspection, small examples) or Parquet (large runs).

Semantics do not depend on the format. A table is addressed by its base path without suffix
(e.g. data/raw/<session>/can_frames); exactly one of <base>.parquet / <base>.csv exists.

Parquet typing rule (lossless w.r.t. the CSV text):
  * a column whose values are all Python ints  -> int64
  * a column whose values are all Python floats -> float64 (analysis outputs only)
  * every other column                         -> string (str(value), exactly what csv writes)
Reading with as_str=True returns the identical text the CSV reader returns (dtype=str,
keep_default_na=False), so validators and analysis see the same values for both formats.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

FORMATS = ("csv", "parquet")
PARQUET_COMPRESSION = "zstd"


def table_file(base: Path) -> Path | None:
    for ext in (".parquet", ".csv"):
        p = Path(str(base) + ext)
        if p.exists():
            return p
    return None


def exists(base: Path) -> bool:
    return table_file(base) is not None


def table_format(base: Path) -> str | None:
    f = table_file(base)
    return None if f is None else f.suffix.lstrip(".")


def _parquet_table(columns: list[str], rows: list[dict]):
    import pyarrow as pa
    arrays, fields = [], []
    for c in columns:
        vals = [r[c] for r in rows]
        if vals and all(type(v) is int for v in vals):
            arrays.append(pa.array(vals, type=pa.int64()))
            fields.append(pa.field(c, pa.int64()))
        elif vals and all(type(v) is float for v in vals):
            arrays.append(pa.array(vals, type=pa.float64()))
            fields.append(pa.field(c, pa.float64()))
        else:
            arrays.append(pa.array(["" if v is None else str(v) for v in vals], type=pa.string()))
            fields.append(pa.field(c, pa.string()))
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def write_table(base: Path, columns: list[str], rows: list[dict], fmt: str) -> Path:
    if fmt not in FORMATS:
        raise ValueError(f"unknown storage format {fmt}")
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    other = Path(str(base) + (".csv" if fmt == "parquet" else ".parquet"))
    if other.exists():                                  # never leave two copies of one table
        other.unlink()
    for r in rows:
        extra = set(r) - set(columns)
        if extra:
            raise ValueError(f"{base.name}: unexpected fields {sorted(extra)}")
    if fmt == "csv":
        path = Path(str(base) + ".csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=columns, extrasaction="raise")
            w.writeheader()
            w.writerows(rows)
        return path
    import pyarrow.parquet as pq
    path = Path(str(base) + ".parquet")
    pq.write_table(_parquet_table(columns, rows), path, compression=PARQUET_COMPRESSION)
    return path


def write_frame(base: Path, df: pd.DataFrame, fmt: str) -> Path:
    """Write an analysis DataFrame (numeric dtypes kept as int64/float64 in Parquet)."""
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    other = Path(str(base) + (".csv" if fmt == "parquet" else ".parquet"))
    if other.exists():
        other.unlink()
    if fmt == "csv":
        path = Path(str(base) + ".csv")
        df.to_csv(path, index=False)
    else:
        path = Path(str(base) + ".parquet")
        df.to_parquet(path, index=False, compression=PARQUET_COMPRESSION)
    return path


def read_table(base: Path, columns: list[str] | None = None, as_str: bool = True, nrows: int | None = None) -> pd.DataFrame:
    f = table_file(Path(base))
    if f is None:
        raise FileNotFoundError(f"no table at {base}(.csv|.parquet)")
    if f.suffix == ".csv":
        kw = dict(keep_default_na=False, na_filter=False, dtype=str) if as_str else {}
        return pd.read_csv(f, usecols=columns, nrows=nrows, **kw)
    df = pd.read_parquet(f, columns=columns)
    if nrows is not None:
        df = df.head(nrows)
    if as_str:
        df = df.astype(str) if len(df.columns) else df
    return df


def read_columns(base: Path) -> list[str]:
    f = table_file(Path(base))
    if f.suffix == ".csv":
        return list(pd.read_csv(f, nrows=0).columns)
    import pyarrow.parquet as pq
    return list(pq.read_schema(f).names)
