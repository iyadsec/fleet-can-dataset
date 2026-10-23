# Validation methods

All checks run on **all 835 sessions** (no sampling), except the descriptor-compatibility comparison against the
slow reference implementation (one session per scenario × family × N × route-group cell) and two release-wide
pytest checks that sample every 40th session (the same properties are checked on every session in
`validation/P1_temporal_consistency.csv`).

| Area | Method | Output |
|---|---|---|
| Source integrity | `sha256sum -c` of the frozen SHA256SUMS (26,740 files) + SHA-256 of SHA256SUMS, manifest and matrix vs FREEZE.json; repeated after export | `validation/source_integrity.csv`, `source_integrity_after_export.csv` |
| Completeness | manifest vs directories (raw, ground_truth, metadata, validation), 5 repetitions per configuration, generator verdict, matrix SHA | `validation/source_manifest_validation.csv` |
| Schema / formats | column names and types; nulls; frame_uid = `<sid>-F<index>`; text timestamps derivable from integer times; channel ↔ vehicle; CAN-ID format and 11-bit range; DLC 0..8; payload hex and length = 2·DLC; frame bits within Classical-CAN bounds and tx duration = bits × 2 µs; ordering (global and per vehicle), ties, duplicates; vCAN unmatched captures | `validation/source_schema_validation.csv` |
| Export fidelity | Parquet and CSV read back and compared with the in-memory table built from the source (exact equality) | same file, `parquet_roundtrip_equal`, `csv_roundtrip_equal` |
| Ground truth | label alignment, label ⇔ attack ID ⇔ frame source, malicious frames only on attacked vehicles and inside configured intervals, captured = labelled, generated = transmitted + dropped, drops = bus drops, family = configuration, membership = targets | `validation/source_ground_truth_validation.csv`, `results/ground_truth_checks.csv` |
| Campaigns | membership consistency, size, missing observable evidence; configured vs observable onset spread and order | `validation/source_campaign_validation.csv`, `results/campaign_*.csv` |
| Replay | per replay instance: links exist, same ID/payload/bus, exact time shift (start − w0 + offset), originals benign and before start, link set = eligible source set, generator first injection = predicted; onset decomposition into ECU activation / availability / bus delay; classification | `validation/source_replay_validation.csv`, `validation/P2_replay_validation.csv`, `results/replay_*.csv` |
| Metadata leakage | adapter allow-list, injected prohibited fields, descriptor invariance, ground-truth inaccessibility, graph-input guard, model-input allow-list; dataset-wide schema scan | `tests/test_metadata_leakage.py`, `validation/P1_metadata_leakage.csv` |
| Temporal | window rule, timestamp = last frame, ordering and tie-break, observable-only inputs, majority rule; per-session window counts, monotone descriptor timestamps, timestamp = frame time | `tests/test_temporal_consistency.py`, `validation/P1_temporal_consistency.csv` |
| Descriptors | equivalence with the reference implementation | `validation/descriptor_compatibility.csv` |
| Benign behaviour | dataset-wide characterisation; benign-only Isolation Forest (original implementation) on held-out S0 sessions | `results/benign_*.csv`, `validation/P1_benign_sanity_check.csv` |
| G0 / identity bias | G0 vs P1/P2 benign metrics per profile (matched N = 20 and all N), composition, descriptor features; attack rate per vehicle ID (χ² uniformity, Spearman), campaign member positions (KS) | `results/G0_route_bias_statistics.csv`, `results/vehicle_identity_bias.csv`, `validation/P2_G0_bias_assessment.csv` |
| Output integrity | MANIFEST + SHA256SUMS; re-hash; 835 sessions per area; 444,333,222 frames in observations, labels and bus timing | `validation/output_integrity.csv` |

A generator-validation PASS is not by itself a claim of research readiness; the decision is in
`reports/SCIENTIFIC_READINESS_DECISION.md`.
