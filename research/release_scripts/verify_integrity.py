"""Build (build) or verify (verify) MANIFEST.csv and SHA256SUMS of the release.

Wrapper around `research.integrity` (copied in this directory). Environment: FLEETCAN_SRC = source data root of
fleetcan-sim-scale-1.0 (directory containing raw/, ground_truth/, metadata/, validation/, final/);
FLEETCAN_RESEARCH_BASE = directory that contains (or will contain) fleetcan-sim-research-1.0/ and work/.
Usage: python scripts/verify_integrity.py --help
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research.integrity import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
