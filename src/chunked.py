"""Bounded-memory table writer (generator 1.3.0, Milestone 8.1).

ChunkedTable accepts rows one at a time, keeps at most `chunk_rows` of them in memory and writes the same final
table that storage.write_table would write for the full list of rows:

  Parquet  rows are flushed as row groups of a temporary all-text Parquet file (<base>.parquet.part). While
           streaming, the writer tracks the frozen typing rule of storage._parquet_table per column
           (all values Python int -> int64; all values Python float -> float64; otherwise text, None -> "").
           finalize() converts the temporary file ONE ROW GROUP AT A TIME into the final ZSTD Parquet file with
           those types (text -> int64/float64 casts are exact: str(int) and repr(float) round-trip), then
           deletes the temporary file. The whole table is never held in memory.
  CSV      rows are appended to <base>.csv.part with the same csv.DictWriter settings; finalize() renames it.

Chunk size is an implementation detail: it changes row-group boundaries of the final file, never its rows or
values (tests/test_m81.py).
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from . import storage

DEFAULT_CHUNK_ROWS = 50_000


class ChunkedTable:
    def __init__(self, base: Path, columns: list[str], fmt: str, chunk_rows: int = DEFAULT_CHUNK_ROWS):
        if fmt not in storage.FORMATS:
            raise ValueError(f"unknown storage format {fmt}")
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be >= 1")
        self.base, self.columns, self.fmt, self.chunk_rows = Path(base), list(columns), fmt, chunk_rows
        self.base.parent.mkdir(parents=True, exist_ok=True)
        self.final = Path(str(self.base) + "." + fmt)
        self.part = Path(str(self.final) + ".part")
        for p in (self.part, Path(str(self.base) + (".csv" if fmt == "parquet" else ".parquet"))):
            if p.exists():
                p.unlink()
        self._colset = set(self.columns)
        self._buf: list[dict] = []
        self.rows = 0
        self.flushes = 0
        self.max_buffered = 0
        self._all_int = {c: True for c in self.columns}
        self._all_float = {c: True for c in self.columns}
        self._writer = None
        self._fh = None
        self._finalized = False

    # ------------------------------------------------------------------ streaming
    def add(self, row: dict) -> None:
        extra = set(row) - self._colset
        if extra:
            raise ValueError(f"{self.base.name}: unexpected fields {sorted(extra)}")
        self._buf.append(row)
        if len(self._buf) > self.max_buffered:
            self.max_buffered = len(self._buf)
        if len(self._buf) >= self.chunk_rows:
            self.flush()

    def extend(self, rows) -> None:
        for r in rows:
            self.add(r)

    def flush(self) -> None:
        if not self._buf:
            return
        rows, self._buf = self._buf, []
        self.rows += len(rows)
        self.flushes += 1
        if self.fmt == "csv":
            if self._fh is None:
                self._fh = open(self.part, "w", newline="")
                self._csv = csv.DictWriter(self._fh, fieldnames=self.columns, extrasaction="raise")
                self._csv.writeheader()
            self._csv.writerows(rows)
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        arrays = []
        for c in self.columns:
            vals = [r[c] for r in rows]
            if self._all_int[c] and not all(type(v) is int for v in vals):
                self._all_int[c] = False
            if self._all_float[c] and not all(type(v) is float for v in vals):
                self._all_float[c] = False
            arrays.append(pa.array(["" if v is None else str(v) for v in vals], type=pa.string()))
        table = pa.Table.from_arrays(arrays, schema=pa.schema([pa.field(c, pa.string()) for c in self.columns]))
        if self._writer is None:
            self._writer = pq.ParquetWriter(self.part, table.schema, compression=storage.PARQUET_COMPRESSION)
        self._writer.write_table(table)

    # ------------------------------------------------------------------ finalisation
    def finalize(self) -> Path:
        if self._finalized:
            return self.final
        self.flush()
        self._finalized = True
        if self.fmt == "csv":
            if self._fh is None:                           # empty table: header only (as csv.DictWriter writes)
                with open(self.part, "w", newline="") as fh:
                    csv.DictWriter(fh, fieldnames=self.columns).writeheader()
            else:
                self._fh.close()
            os.replace(self.part, self.final)
            return self.final
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        if self._writer is None:                           # no rows: every column is text (frozen rule)
            storage.write_table(self.base, self.columns, [], "parquet")
            return self.final
        self._writer.close()
        types = {c: pa.int64() if self._all_int[c] else pa.float64() if self._all_float[c] else pa.string() for c in self.columns}
        schema = pa.schema([pa.field(c, types[c]) for c in self.columns])
        src = pq.ParquetFile(self.part)
        out = pq.ParquetWriter(self.final, schema, compression=storage.PARQUET_COMPRESSION)
        try:
            for i in range(src.num_row_groups):
                t = src.read_row_group(i)
                cols = [t.column(c) if types[c] == pa.string() else pc.cast(t.column(c), types[c]) for c in self.columns]
                out.write_table(pa.Table.from_arrays(cols, schema=schema))
        finally:
            out.close()
        self.part.unlink()
        return self.final

    def abort(self) -> None:
        """Release file handles without producing a final table (failed run)."""
        if self._writer is not None:
            self._writer.close()
        if self._fh is not None:
            self._fh.close()
        self._finalized = True
