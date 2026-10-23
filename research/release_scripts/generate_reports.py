"""Figures and searchable HTML reports (research.reports); then the PDF (research.pdf_report, needs reportlab).

Environment: FLEETCAN_RESEARCH_BASE = directory containing fleetcan-sim-research-1.0/.
Usage: python scripts/generate_reports.py [--pdf]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research import reports  # noqa: E402
from research.common import RELEASE  # noqa: E402

if __name__ == "__main__":
    reports.main()
    if "--pdf" in sys.argv:
        from research import pdf_report
        pdf_report.build(RELEASE / "reports")
