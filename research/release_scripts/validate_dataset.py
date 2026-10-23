"""Leakage and temporal tests, dataset-wide leakage scan, descriptor compatibility, benign-only Isolation Forest sanity check.

Wrapper around `research.validate` (copied in this directory). Environment: FLEETCAN_SRC = source data root of
fleetcan-sim-scale-1.0 (directory containing raw/, ground_truth/, metadata/, validation/, final/);
FLEETCAN_RESEARCH_BASE = directory that contains (or will contain) fleetcan-sim-research-1.0/ and work/.
Usage: python scripts/validate_dataset.py --help
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research.validate import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
