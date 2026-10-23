# Citation

If you use this dataset, please cite the dataset release and state the version:

```
Atieh, I. (2026). FLEETCAN-SIM research dataset (fleetcan-sim-research-1.0): a synthetic multi-vehicle CAN dataset
with coordinated attack campaigns, derived from fleetcan-sim-scale-1.0. University of Reading.
Version fleetcan-sim-research-1.0.
```

BibTeX:

```bibtex
@misc{atieh2026fleetcansimresearch,
  author       = {Atieh, Iyad},
  title        = {{FLEETCAN-SIM} research dataset (fleetcan-sim-research-1.0): a synthetic multi-vehicle {CAN}
                  dataset with coordinated attack campaigns},
  year         = {2026},
  publisher    = {University of Reading},
  note         = {Derived release of fleetcan-sim-scale-1.0 (SHA256SUMS 5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70)}
}
```

Add the persistent identifier (DOI) of the repository where the release is deposited once it is assigned.

Required statements when reporting results on this dataset:
* the data are **synthetic** (SUMO mobility + synthetic CAN generation over a logical CAN bus and SocketCAN vCAN);
* they contain **no production ECU captures**, and real-world generalisation has not been established;
* the evaluation used only the detector-safe observation schema (no metadata/ground truth as features);
* which window-label rule was used (original majority rule or "any malicious frame").
