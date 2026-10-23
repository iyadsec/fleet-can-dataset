# Reproducibility

## Inputs

* Source: `fleetcan-sim-scale-1.0` (frozen, read-only). SHA256SUMS
  `5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70` (26,740 files, 21,727,934,640 bytes),
  manifest `e5138551005b82b4370780e47efbec5792b770d34bcc61db47955a068de012c6`, matrix
  `9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735`. Obtain it from the GitHub release
  `dataset-fleetcan-sim-scale-1.0` of `iyadsec/fleet-can-dataset` (see that repository's README).
* Code: repository `iyadsec/fleet-can-dataset`, directory `research/` (+ `analysis/`, `detection/` it imports);
  the exact commit is recorded in `documentation/BUILD_INFO.json`. A copy of every script is in `scripts/`.
* Environment: Python 3.12.3, numpy 2.5.4, pandas 2.3.3, pyarrow 21.0.0, scikit-learn 1.9.1, scipy 1.18.1,
  matplotlib (figures), reportlab (PDF only). Platform used: Ubuntu 24.04 aarch64 (UTM VM), 8 vCPU, 8 GB RAM.

## Steps (from the repository root, source under `data/`)

```bash
.venv/bin/python -m research.export_dataset --workers 2      # 835 sessions -> release data/ (resumable)
.venv/bin/python -m research.aggregate                       # results/ and validation/ source tables
.venv/bin/python -m research.validate                        # tests, leakage scan, compatibility, benign IF
.venv/bin/python -m research.reports                         # reports/*.md, reports/html/, figures
python build_pdf.py <release>/reports                        # PDF (needs reportlab)
.venv/bin/python -m research.integrity build                 # MANIFEST.csv + SHA256SUMS
.venv/bin/python -m research.integrity verify                # re-hash + completeness checks
```

Environment variables: `FLEETCAN_SRC` (source data root, default `<repo>/data`), `FLEETCAN_RESEARCH_BASE`
(output base, default `~/fleetcan_research`).

## Determinism

* Every per-session output is a pure function of that session's frozen source files (no randomness, no wall
  clock). Row order: source order; descriptor windows: (vehicle, window index).
* The benign Isolation Forest check uses `random_state = 42` and the deterministic session split
  (`SeedSequence([42, crc32(config_id)])`); it was run twice and the scores compared
  (`validation/P1_benign_sanity_check.csv`).
* Writes are atomic (temporary file + rename); an interrupted export resumes from per-session completion markers.
* Parquet: zstd level 9, DELTA_BINARY_PACKED for integer columns (lossless). CSV: pyarrow writer, strings quoted only
  when needed, gzip.

## Integrity

`SHA256SUMS` (sha256sum -c compatible) and `MANIFEST.csv` (path, bytes, SHA-256, area, session, format, source
mapping) cover every release file. `validation/output_integrity.csv` records the re-verification. The source dataset
was re-hashed before export and after export (`validation/source_integrity.csv`,
`validation/source_integrity_after_export.csv`).
