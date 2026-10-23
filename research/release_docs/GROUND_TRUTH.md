# Ground truth

Ground truth is **evaluation-only** and lives in `data/ground_truth/<sid>/`, physically separate from the
detector-facing observations. All original ground-truth information is preserved; the release only adds join keys
(`frame_index`, `replay_frame_index`, `original_frame_index`) and clearly marked derived tables.

## Original (from the generator, unchanged values)

* **Frame labels** — every frame: benign/malicious, attack type, attack ID, campaign ID, frame source, message name.
* **Attacks** — configured start/end (`configured_start`, `start_ns`, `end_ns`), parameters, seed, requested rate,
  generated / transmitted / dropped / queue_dropped / expired_dropped / captured / skipped_inactive counts,
  first/last generated time.
* **Campaigns** — coordinated flag, delay pattern, target vehicles, configured campaign start/end, duration,
  inter-vehicle delays, common overlap.
* **Campaign membership** — per member: attack ID, configured vehicle attack start/end, relative delay.
* **Campaign overlap**, **attack frames**, **replay map** (replayed frame → original frame), **dropped frames**.

## Derived (this release; never replaces configured values)

* `attack_instances.*`: per attack the configured interval **and** the observable timing — first/last malicious
  frame (`frame_index`, `timestamp_ns`, generation time), onset/end delay, first descriptor window containing
  ≥ 1 / ≥ 5 / ≥ 50 frames of the attack and its timestamp, number of attack-containing and majority windows,
  injection rate, bus utilisation and benign-rate change during the attack. Prefix `derived_`.
* `window_labels.*`: per descriptor window malicious frame count/ratio, the original FLEET-GUARD majority label
  (`label_majority_original`), the "any malicious frame" label (`label_any_malicious`), window class and the
  attack/campaign IDs present.

## Validation (all 835 sessions)

Frame/label alignment, label consistency (malicious ⇔ attack ID, frame source), malicious frames only on attacked
vehicles and inside the configured interval, captured counts = labelled frames, generated = transmitted + dropped,
campaign membership = campaign targets, replay links (existence, same ID/payload/bus, exact time shift, originals
benign and before start, link set = eligible source set). Results: `validation/source_ground_truth_validation.csv`,
`validation/source_campaign_validation.csv`, `validation/source_replay_validation.csv`,
`results/ground_truth_checks.csv`.

## Labelling rules for descriptor windows

The original FLEET-GUARD `_window_label` is the **majority** rule (malicious if ≥ 50 of 100 frames are malicious;
tie → malicious) — stored as `label_majority_original`. `label_any_malicious` (≥ 1 malicious frame) is the
"Evaluation A" positive definition used in this repository's M7 protocol and is provided as a separate, documented
alternative. Which rule an experiment uses is an evaluation decision (see `reports/descriptor_methodology.md`);
the release does not choose for the user.
