"""Bounded-memory access to large session tables (generator 1.3.0, Milestone 8.1).

The validator and the analysis layer process a session one group of vehicles at a time instead of loading
whole-session tables. Rows are read sequentially in file order (Parquet record batches / CSV chunks) and filtered
on one column, so the returned rows are exactly the rows storage.read_table(..., as_str=True) would return for that
filter, in the same order and with the same text representation.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import storage

GROUP_VEHICLES = 5            # vehicles per group (implementation detail; results do not depend on it)
BATCH_ROWS = 131_072


def release_memory() -> None:
    """Return memory freed after a vehicle group to the OS (Python GC, Arrow pool, glibc heap). Results are unaffected;
    without it, RSS keeps the high-water mark of earlier groups through allocator fragmentation."""
    import ctypes
    import gc
    gc.collect()
    try:
        import pyarrow as pa
        pa.default_memory_pool().release_unused()
    except Exception:
        pass
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def vehicle_groups(vehicle_ids, size: int = GROUP_VEHICLES) -> list[list[str]]:
    ids = sorted(vehicle_ids)
    return [ids[i:i + size] for i in range(0, len(ids), max(1, size))]


def read_filtered(base: Path, column: str, values, columns: list[str] | None = None) -> pd.DataFrame:
    """Rows whose `column` is in `values`, as text, in file order."""
    f = storage.table_file(Path(base))
    if f is None:
        raise FileNotFoundError(f"no table at {base}(.csv|.parquet)")
    values = set(values)
    cols = None if columns is None else list(dict.fromkeys([*columns]))
    if f.suffix == ".csv":
        parts = []
        for chunk in pd.read_csv(f, dtype=str, keep_default_na=False, na_filter=False, chunksize=BATCH_ROWS):
            sel = chunk[chunk[column].isin(values)]
            parts.append(sel if cols is None else sel[cols])
        if not parts:
            return pd.read_csv(f, dtype=str, keep_default_na=False, na_filter=False, nrows=0, usecols=cols)
        return pd.concat(parts, ignore_index=True)
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(f)
    read_cols = None if cols is None else list(dict.fromkeys([*cols, column]))
    vals = pa.array(sorted(values), type=pf.schema_arrow.field(column).type) if values else None
    parts = []
    for batch in pf.iter_batches(batch_size=BATCH_ROWS, columns=read_cols):
        if vals is None:
            continue
        sel = batch.filter(pc.is_in(batch.column(column), value_set=vals))
        if sel.num_rows:
            parts.append(sel)
    schema = pf.schema_arrow if read_cols is None else pa.schema([pf.schema_arrow.field(c) for c in read_cols])
    table = pa.Table.from_batches(parts, schema=schema) if parts else schema.empty_table()
    df = table.to_pandas()
    if cols is not None:
        df = df[cols]
    return df.astype(str) if len(df.columns) else df


def distinct(base: Path, column: str) -> list[str]:
    """Sorted distinct text values of one column (one column in memory at a time)."""
    f = storage.table_file(Path(base))
    if f.suffix == ".csv":
        out = set()
        for chunk in pd.read_csv(f, dtype=str, keep_default_na=False, na_filter=False, usecols=[column], chunksize=BATCH_ROWS):
            out.update(chunk[column].unique())
        return sorted(out)
    import pyarrow.parquet as pq
    col = pq.read_table(f, columns=[column]).column(column).unique().to_pylist()
    return sorted(str(v) for v in col)


def iter_column_batches(base: Path, columns: list[str]):
    """Yield DataFrames (text) of `columns`, sequentially, in file order."""
    f = storage.table_file(Path(base))
    if f.suffix == ".csv":
        yield from pd.read_csv(f, dtype=str, keep_default_na=False, na_filter=False, usecols=columns, chunksize=BATCH_ROWS)
        return
    import pyarrow.parquet as pq
    for batch in pq.ParquetFile(f).iter_batches(batch_size=BATCH_ROWS, columns=columns):
        yield batch.to_pandas().astype(str)


def num_rows(base: Path) -> int:
    f = storage.table_file(Path(base))
    if f.suffix == ".csv":
        return sum(len(c) for c in pd.read_csv(f, dtype=str, usecols=[0], chunksize=BATCH_ROWS))
    import pyarrow.parquet as pq
    return pq.ParquetFile(f).metadata.num_rows
