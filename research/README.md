# research/ — fleetcan-sim-research-1.0 derived release

Builds the research release from the frozen fleetcan-sim-scale-1.0 (read-only). Steps and determinism:
`research/release_docs/REPRODUCIBILITY.md`. Entry points: `export_dataset`, `aggregate`, `validate`, `issues`,
`reports`, `pdf_report`, `integrity` (run as `.venv/bin/python -m research.<module>`). Detector-side code must use
`research/adapter.py`. Tests: `tests/test_metadata_leakage.py`, `tests/test_temporal_consistency.py`.
`release_docs/`, `reports_md/` and `release_scripts/` are the sources of the release documentation, the
readiness reports and the release `scripts/` wrappers.
