# Descriptor methodology and compatibility

**Result: COMPATIBLE.** The release descriptors match the reference FLEET-GUARD implementation, a reproduction of
`iyadsec/fleet-can-ids @ 4f1ebf3`, in every compared session.

**Coverage.** 77 sessions were compared, one per scenario × family × N × route-group cell. That covers 638,594 windows
and 15,326,256 feature values.

| Check | Result |
|---|---|
| Window count, (vehicle, window) keys, first/last frame, window start, descriptor timestamp, NaN-fill count | identical in 77/77 sessions |
| 24 features, tolerance 1e-9 relative | 77/77 within tolerance; 14,756,735 of 15,326,256 values (96.3 %) bit-identical |
| Largest absolute difference | 2.7e-15 (`can_id_entropy`; floating-point summation order) |
| Window labels: malicious count, class, attack IDs, campaign IDs, first malicious time | identical in 77/77 sessions |
| Original majority rule (ratio ≥ 0.5, tie → malicious) | identical |

The definition is unchanged: feature names and order, CAN-ID entropy (+1e-12), inter-arrival in seconds, DLC
statistics, bytes ≥ DLC set to NaN before nan-statistics, `fillna(0)` and windows of 100 with stride 50. Full table:
`documentation/DESCRIPTOR_COMPATIBILITY.md`.

**Export.** Descriptors were generated for all 835 sessions: 8,814,858 windows in `data/descriptors/<sid>/`. Labels
are kept separately in `data/ground_truth/<sid>/window_labels.*`. Statistics per profile and window class are in
`results/descriptor_statistics.csv`. The raw CAN data are untouched.

## Window labelling — tests and the open decision

| Window content | `label_majority_original` | `label_any_malicious` |
|---|---|---|
| 0 malicious frames | 0 | 0 |
| 1 / 5 / 49 malicious frames | 0 | 1 |
| 50 malicious frames (tie) | 1 | 1 |
| > 50 malicious frames | 1 | 1 |

These cases are tested in `tests/test_temporal_consistency.py::test_original_majority_label_rule`. The release-wide
check (`test_release_window_labels_follow_original_rule`) confirms both columns follow their rules.

**The original rule is unambiguous, but has consequences that need an explicit evaluation decision (FR-08):**

* **Malfunction.** At 100 frames/s against 270–350 benign frames/s, a window never holds more than 28 malfunction
  frames. None of the 2,755 malfunction instances has a majority window, so the original rule makes malfunction
  undetectable by definition.
* **Replay.** Replay interleaves 1:1 with benign traffic, so at most 50 of 100 frames are replay frames. Replay
  windows reach the majority label only through the tie rule.
* **Flooding and fuzzy.** These reach 75–87 attack frames per window.

The repository's M7 protocol used "any malicious frame" (Evaluation A) as the primary positive definition and the
majority rule (Evaluation B) as the original-compatible secondary one. **This release stores both and does not
choose. The rule for FLEET-GUARD / ROBUST-FLEET-GUARD needs approval.**
