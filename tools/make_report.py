"""夜間開発の報告書(PDF)を作る。使い方: python3 tools/make_report.py <out.pdf> <journal.md> <metrics.txt> [tests.txt]"""
import datetime, sys
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# ヒラギノ(.ttc)は CFF アウトラインで reportlab が使えないため、TrueType の Arial Unicode(日本語を含む)を使う
import os
_F = next(p for p in ("/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf") if os.path.exists(p))
pdfmetrics.registerFont(TTFont("JP", _F))
pdfmetrics.registerFont(TTFont("JPB", _F))
INK, MUTED, ACC = colors.HexColor("#1a1a1a"), colors.HexColor("#666666"), colors.HexColor("#0b6b4f")
base = ParagraphStyle("b", fontName="JP", fontSize=10, leading=16, textColor=INK, wordWrap="CJK")
h1 = ParagraphStyle("h1", parent=base, fontName="JPB", fontSize=20, leading=26, spaceAfter=4)
h2 = ParagraphStyle("h2", parent=base, fontName="JPB", fontSize=13, leading=18, spaceBefore=14, spaceAfter=6, textColor=ACC)
small = ParagraphStyle("s", parent=base, fontSize=8.5, leading=13, textColor=MUTED)
cell = ParagraphStyle("c", parent=base, fontSize=9, leading=13)
cellb = ParagraphStyle("cb", parent=cell, fontName="JPB")


def P(t, s=base): return Paragraph(t, s)


def table(rows, widths, head=True):
    data = [[P(str(c), cellb if (head and i == 0) else cell) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if head: st.append(("LINEBELOW", (0, 0), (-1, 0), 0.8, INK))
    t.setStyle(TableStyle(st))
    return t


def footer(c, d):
    c.saveState(); c.setFont("JP", 8); c.setFillColor(MUTED)
    c.drawString(20 * mm, 10 * mm, "StackChan × Sokutsu 夜間開発報告"); c.drawRightString(190 * mm, 10 * mm, f"{d.page}")
    c.restoreState()


def main(out, journal, metrics, tests=""):
    story = [P("StackChan × Sokutsu 夜間開発報告", h1),
             P(f"対象期間 2026-11-11 03:00 〜 07:00 / 作成 {datetime.datetime.now():%Y-%m-%d %H:%M}", small), Spacer(1, 6)]
    story += [P("1. 要約", h2), P(open("/tmp/_summary.txt", encoding="utf-8").read().replace("\n", "<br/>") if False else SUMMARY)]
    story += [P("2. ご要望への対応", h2), table(REQS, [48, 112])]
    story += [P("3. 時系列(開発ジャーナル)", h2)]
    for line in open(journal, encoding="utf-8"):
        line = line.strip()
        if line.startswith("- "):
            story.append(P("・" + line[2:].replace("&", "&amp;").replace("<", "&lt;")))
    story += [P("4. 計測結果", h2)]
    rows = [["指標", "値"]] + [[l.split(": ", 1)[0], l.split(": ", 1)[1]] for l in open(metrics, encoding="utf-8").read().splitlines() if ": " in l]
    story.append(table(rows, [70, 90]))
    if tests:
        story += [P("5. 単体試験", h2), P(open(tests, encoding="utf-8").read().strip().replace("\n", "<br/>"), small)]
    story += [P("6. 既知の課題と次の提案", h2)]
    for t in ISSUES: story.append(P("・" + t))
    SimpleDocTemplate(out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                      title="StackChan × Sokutsu 夜間開発報告", author="Claude Code").build(story, onFirstPage=footer, onLaterPages=footer)


SUMMARY = "(要約は最終版で入ります)"
REQS = [["要望", "対応"]]
ISSUES = ["(最終版で入ります)"]
exec(open(sys.argv[0].replace("make_report.py", "report_content.py"), encoding="utf-8").read()) if __import__("os").path.exists(sys.argv[0].replace("make_report.py", "report_content.py")) else None

if __name__ == "__main__":
    main(*sys.argv[1:])
