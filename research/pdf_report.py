"""Render reports/validation_report_source.md (+ figures) to reports/FLEETCAN_SIM_Research_Dataset_Validation.pdf.

Needs reportlab (pip install reportlab). Usage: python -m research.pdf_report <release>/reports
Markdown subset: #/##/### headings, paragraphs, bullet/numbered lists, pipe tables, **bold**, *italic*, `code`,
and image lines of the form ![caption](figures/NAME.png).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ss = getSampleStyleSheet()
body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
cell = ParagraphStyle("c", parent=body, fontSize=7, leading=8.6)
bullet = ParagraphStyle("u", parent=body, leftIndent=12, bulletIndent=2)
cap = ParagraphStyle("cap", parent=body, fontSize=8, textColor=colors.HexColor("#444444"), alignment=1)
h1, h2, h3 = ss["Title"], ss["Heading2"], ss["Heading3"]


def inline(t):
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<i>\1</i>", t)
    t = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", t)
    return t


def build(rep: Path):
    md = (rep / "validation_report_source.md").read_text()
    story, para = [], []

    def flush():
        nonlocal para
        if para:
            story.append(Paragraph(inline(" ".join(para)), body))
            story.append(Spacer(1, 4))
            para = []

    lines, i = md.splitlines(), 0
    while i < len(lines):
        l = lines[i]
        img = re.match(r"^!\[(.*)\]\((.+)\)\s*$", l)
        if l.strip() == "\\pagebreak":
            flush(); story.append(PageBreak())
        elif img:
            flush()
            p = rep / img.group(2)
            if p.exists():
                im = Image(str(p))
                w = 16 * cm
                im.drawHeight, im.drawWidth = im.drawHeight * w / im.drawWidth, w
                if im.drawHeight > 11 * cm:
                    s = 11 * cm / im.drawHeight
                    im.drawHeight, im.drawWidth = im.drawHeight * s, im.drawWidth * s
                story.append(KeepTogether([im, Paragraph(inline(img.group(1)), cap), Spacer(1, 6)]))
        elif l.startswith("# "):
            flush(); story.append(Paragraph(inline(l[2:]), h1))
        elif l.startswith("## "):
            flush(); story.append(Paragraph(inline(l[3:]), h2))
        elif l.startswith("### "):
            flush(); story.append(Paragraph(inline(l[4:]), h3))
        elif l.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[-| :]+\|$", lines[i]):
                    rows.append([Paragraph(inline(c.strip()), cell) for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = Table(rows, repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(t); story.append(Spacer(1, 6))
            continue
        elif re.match(r"^\s*(\* |- |\d+\. )", l):
            flush()
            ind = len(l) - len(l.lstrip())
            txt = re.sub(r"^\s*(\* |- |\d+\. )", "", l)
            while i + 1 < len(lines) and lines[i + 1].startswith("  ") and not re.match(r"^\s*(\* |- |\d+\. )", lines[i + 1]):
                i += 1
                txt += " " + lines[i].strip()
            st = ParagraphStyle("u%d" % ind, parent=bullet, leftIndent=12 + ind * 6, bulletIndent=2 + ind * 6)
            story.append(Paragraph(inline(txt), st, bulletText="•" if ind == 0 else "–"))
        elif not l.strip():
            flush()
        else:
            para.append(l.strip())
        i += 1
    flush()

    def footer(c, d):
        c.saveState()
        c.setFont("Helvetica", 7)
        c.drawString(1.8 * cm, 1.0 * cm, "fleetcan-sim-research-1.0 — scientific validation report (synthetic data)")
        c.drawRightString(A4[0] - 1.8 * cm, 1.0 * cm, f"page {d.page}")
        c.restoreState()

    doc = SimpleDocTemplate(str(rep / "FLEETCAN_SIM_Research_Dataset_Validation.pdf"), pagesize=A4,
                            leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                            title="FLEETCAN-SIM research dataset validation (fleetcan-sim-research-1.0)", author="Iyad Atieh")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print("pdf written")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
