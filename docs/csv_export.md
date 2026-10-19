# CSV export of fleetcan-sim-scale-1.0

The frozen dataset is stored as Parquet. For inspection or tools that need plain CSV,
`scripts/export_csv_subset.py` writes selected sessions as CSV. It only reads the frozen files. Files are organised by
the dataset's main structure: **scenario / attack / fleet size**.

```
<scenario>/<attack>/N<NNN>/<session_id>_can_frames.csv   raw observable CAN frames (all columns of raw/<sid>/can_frames)
<scenario>/<attack>/N<NNN>/<session_id>_labels.csv       per-frame ground truth (ground_truth/<sid>/frame_labels)
```

**Folder names:**
* Scenario: `S0_benign`, `S1_isolated`, `S2_independent`, `S3_coordinated`, `S3_mixed`, `S3_replay_probe`.
* Attack: `none`, `flooding`, `fuzzy`, `malfunction`, `replay`.
* Fleet size: `N020`, `N050`, `N100`, and `N010` (the G0 bridge sessions).

**Built-in checks.** Every written CSV is re-read and compared with its Parquet source, every row and every value as
text. Its SHA-256 goes into `export_manifest.jsonl`. `scripts/verify_csv_export.py` (Python standard library only)
re-checks every file against those hashes on the target machine, then writes `index.csv` (one row per session: N, K,
scenario, attack, timing, seed, frames, paths, SHA-256) and a README.

**Export made on 2026-10-19 (Mac, `~/fleetcan_csv`):**
* Content: repetition R1 of all 167 configurations, i.e. 167 sessions with 88,858,703 frames.
* Files: 334 CSV files, 37.76 GB.
* Verification: PASS.

```bash
# on the machine with the dataset (example: R1 of every configuration, in batches)
.venv/bin/python scripts/export_csv_subset.py --out /path/to/csv --repetition 1 [--start 0 --count 10]
# on the machine holding the CSV
python3 scripts/verify_csv_export.py /path/to/csv --expect-sessions 167 --expect-frames 88858703
```

Size guide: plain CSV takes about 253 bytes per frame for `can_frames` and about 169 bytes per frame for labels. All
835 sessions would need about 187 GB.

**Detector-safe use:** `scenario_id`, `session_id`, `frame_uid` and the `*_labels.csv` files are not detector input.
The Parquet release remains the authoritative dataset.
