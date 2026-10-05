"""Build the Chinese PDF manual from docs/user-guide.md.

Run with a Python environment containing reportlab. The PDF is checked with
Poppler after generation; the Markdown remains the single editable source.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "user-guide.md"
OUTPUT = ROOT / "output" / "pdf" / "user-guide.pdf"


def register_fonts() -> None:
    candidates = [
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
    ]
    font = next((path for path in candidates if path.is_file()), None)
    if font is None:
        raise FileNotFoundError("No Chinese TTF font available for PDF generation")
    pdfmetrics.registerFont(TTFont("GuideCJK", str(font)))
    pdfmetrics.registerFontFamily("GuideCJK", normal="GuideCJK", bold="GuideCJK",
                                 italic="GuideCJK", boldItalic="GuideCJK")


def inline_markup(source: str) -> str:
    source = html.escape(source)
    source = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", source)
    source = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", source)
    source = re.sub(r"`([^`]+)`", r'<font color="#1F5E91">\1</font>', source)
    return source


def build() -> Path:
    register_fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = SOURCE.read_text(encoding="utf-8")
    normal = ParagraphStyle(
        "Body", fontName="GuideCJK", fontSize=9.6, leading=16.7,
        textColor=colors.HexColor("#20252E"), spaceAfter=8, wordWrap="CJK",
    )
    normal_keep = ParagraphStyle("BodyKeep", parent=normal, keepWithNext=True)
    title = ParagraphStyle(
        "Title", parent=normal, fontSize=23, leading=31, spaceAfter=18,
        textColor=colors.HexColor("#19375A"), alignment=TA_LEFT,
    )
    heading = ParagraphStyle(
        "Heading", parent=normal, fontSize=14.5, leading=23, spaceBefore=17,
        spaceAfter=8, textColor=colors.HexColor("#244B72"), keepWithNext=True,
    )
    bullet = ParagraphStyle(
        "Bullet", parent=normal, leftIndent=18, firstLineIndent=-12, spaceAfter=5,
    )
    caption = ParagraphStyle(
        "Caption", parent=normal, fontSize=8, leading=12, alignment=TA_CENTER,
        textColor=colors.HexColor("#60758B"), spaceAfter=10,
    )
    code_style = ParagraphStyle(
        "Code", fontName="Courier", fontSize=8, leading=12,
        textColor=colors.HexColor("#21354D"), spaceAfter=0,
    )
    story = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph(keep_with_next: bool = False) -> None:
        if paragraph_lines:
            story.append(Paragraph(inline_markup(" ".join(paragraph_lines)),
                                   normal_keep if keep_with_next else normal))
            paragraph_lines.clear()

    def flush_code() -> None:
        if not code_lines:
            return
        block = Preformatted("\n".join(code_lines), code_style)
        table = Table([[block]], colWidths=[A4[0] - 96])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
            ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#D9E3ED")),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.extend([table, Spacer(1, 10)])
        code_lines.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            flush_paragraph(keep_with_next=not in_code)
            if in_code:
                flush_code()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(raw)
            continue
        if not line:
            flush_paragraph()
            continue
        if line == "<!-- pagebreak -->":
            flush_paragraph()
            story.append(PageBreak())
            continue
        image_match = re.fullmatch(r"!\[([^]]+)\]\(([^)]+)\)", line)
        if image_match:
            flush_paragraph()
            image_path = SOURCE.parent / image_match.group(2)
            if not image_path.is_file():
                raise FileNotFoundError(image_path)
            picture = Image(str(image_path))
            max_height = (220 if image_path.name.startswith("recent-files")
                          else 280 if image_path.name.startswith("device-popup") else 310)
            scale = min((A4[0] - 96) / picture.imageWidth, max_height / picture.imageHeight)
            picture.drawWidth = picture.imageWidth * scale
            picture.drawHeight = picture.imageHeight * scale
            story.append(KeepTogether([picture, Spacer(1, 4),
                                       Paragraph(inline_markup(image_match.group(1)), caption)]))
            continue
        if line.startswith("# "):
            flush_paragraph()
            story.append(Paragraph(inline_markup(line[2:]), title))
            continue
        if line.startswith("## "):
            flush_paragraph()
            story.append(Paragraph(inline_markup(line[3:]), heading))
            continue
        if line.startswith("- "):
            flush_paragraph()
            story.append(Paragraph("- " + inline_markup(line[2:]), bullet))
            continue
        paragraph_lines.append(line)
    flush_paragraph()
    flush_code()

    def page_furniture(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D5E0EB"))
        canvas.line(48, A4[1] - 40, A4[0] - 48, A4[1] - 40)
        canvas.setFont("GuideCJK", 8)
        canvas.setFillColor(colors.HexColor("#627890"))
        canvas.drawString(48, A4[1] - 31, "Qubit Sequencer · 0.3.1 用户手册")
        canvas.drawRightString(A4[0] - 48, 30, f"{doc.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=48, rightMargin=48,
        topMargin=57, bottomMargin=48, title="Qubit Sequencer 使用说明",
        author="Qubit Sequencer",
    )
    document.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)
    return OUTPUT


if __name__ == "__main__":
    print(build())
