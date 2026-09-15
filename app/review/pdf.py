from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .service import StudentReport


_FONT_NAMES: tuple[str, str] | None = None
ACCENT = colors.HexColor("#d85e49")
INK = colors.HexColor("#20242d")
MUTED = colors.HexColor("#77746c")
SAGE = colors.HexColor("#e8f0e3")
LILAC = colors.HexColor("#eee8f5")
PAPER = colors.HexColor("#fffefa")


def _font_names() -> tuple[str, str]:
    global _FONT_NAMES
    if _FONT_NAMES is not None:
        return _FONT_NAMES

    font_pairs = (
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/segoeuib.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf")),
    )
    for regular, bold in font_pairs:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("CuratorSans", str(regular)))
            pdfmetrics.registerFont(TTFont("CuratorSans-Bold", str(bold)))
            _FONT_NAMES = ("CuratorSans", "CuratorSans-Bold")
            return _FONT_NAMES

    _FONT_NAMES = ("Helvetica", "Helvetica-Bold")
    return _FONT_NAMES


def _safe_text(value: Any) -> str:
    text = str(value or "").replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
    return escape(text).replace("\n", "<br/>")


def _target_label(target: str) -> str:
    if target == "week":
        return "Неделя"
    if target == "reflection":
        return "Рефлексия"
    kind, _, value = str(target).partition(":")
    if kind == "goal":
        return f"Цель {value}"
    if kind == "task":
        return f"Задача {value}"
    if kind == "day":
        return value
    return str(target)


def _styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    return {
        "title": ParagraphStyle(
            "CuratorPdfTitle", parent=base, fontName=bold, fontSize=24,
            leading=28, textColor=INK, spaceAfter=5,
        ),
        "student": ParagraphStyle(
            "CuratorPdfStudent", parent=base, fontName=regular, fontSize=13,
            leading=17, textColor=MUTED, spaceAfter=18,
        ),
        "summary": ParagraphStyle(
            "CuratorPdfSummary", parent=base, fontName=regular, fontSize=11,
            leading=16, textColor=INK,
        ),
        "section": ParagraphStyle(
            "CuratorPdfSection", parent=base, fontName=bold, fontSize=10,
            leading=13, textColor=MUTED, spaceBefore=18, spaceAfter=8,
            uppercase=True,
        ),
        "body": ParagraphStyle(
            "CuratorPdfBody", parent=base, fontName=regular, fontSize=10.5,
            leading=15, textColor=INK, alignment=TA_LEFT, spaceAfter=7,
        ),
        "rewrite": ParagraphStyle(
            "CuratorPdfRewrite", parent=base, fontName=regular, fontSize=10,
            leading=14, textColor=INK, spaceAfter=8,
        ),
        "footer": ParagraphStyle(
            "CuratorPdfFooter", parent=base, fontName=regular, fontSize=8,
            leading=10, textColor=MUTED,
        ),
    }


def _add_findings(story: list[Any], title: str, findings: list[Any], styles: dict[str, ParagraphStyle]) -> None:
    if not findings:
        return
    story.append(Paragraph(_safe_text(title).upper(), styles["section"]))
    for index, finding in enumerate(findings, 1):
        target = _safe_text(_target_label(finding.target))
        message = _safe_text(finding.message)
        story.append(Paragraph(f"<b>{index}. {target}</b><br/>{message}", styles["body"]))


def _add_rewrites(story: list[Any], report: StudentReport, styles: dict[str, ParagraphStyle]) -> None:
    if not report.review or not report.review.rewrites:
        return
    story.append(Paragraph("КАК СДЕЛАТЬ ФОРМУЛИРОВКИ ЯСНЕЕ", styles["section"]))
    for index, rewrite in enumerate(report.review.rewrites, 1):
        story.append(
            Paragraph(
                f"<b>{index}. {_safe_text(_target_label(rewrite.target))}</b><br/>"
                f"Было: {_safe_text(rewrite.original)}<br/>"
                f"Можно так: <b>{_safe_text(rewrite.suggestion)}</b>",
                styles["rewrite"],
            )
        )


def _add_questions(story: list[Any], report: StudentReport, styles: dict[str, ParagraphStyle]) -> None:
    if not report.review or not report.review.questions:
        return
    story.append(Paragraph("ВОПРОСЫ ДЛЯ САМОПРОВЕРКИ", styles["section"]))
    for index, question in enumerate(report.review.questions, 1):
        story.append(Paragraph(f"<b>{index}.</b> {_safe_text(question)}", styles["body"]))


def _draw_footer(canvas: Any, document: Any, regular: str) -> None:
    canvas.saveState()
    canvas.setFillColor(MUTED)
    canvas.setFont(regular, 8)
    canvas.drawString(document.leftMargin, 10 * mm, "Проверка планов · рекомендации")
    canvas.drawRightString(A4[0] - document.rightMargin, 10 * mm, str(canvas.getPageNumber()))
    canvas.restoreState()


def build_student_recommendations_pdf(report: StudentReport) -> bytes:
    """Build a student-facing PDF without exposing internal scoring details."""
    regular, bold = _font_names()
    styles = _styles(regular, bold)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=18 * mm,
        title=f"Рекомендации — {report.student}",
        author="Проверка планов",
    )
    story: list[Any] = [
        Paragraph("Рекомендации к плану недели", styles["title"]),
        Paragraph(f"{_safe_text(report.student)} · {_safe_text(report.week_label)}", styles["student"]),
    ]

    if report.review and report.review.summary:
        summary = report.review.summary
    elif report.findings:
        summary = "В плане есть несколько пунктов, которые стоит уточнить, чтобы неделю было проще прожить и завершить."
    else:
        summary = "План выглядит хорошо. Продолжай держать задачи конкретными и выполнимыми."
    story.append(
        Table(
            [[Paragraph(_safe_text(summary), styles["summary"])]],
            colWidths=[document.width],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), SAGE),
                ("BOX", (0, 0), (-1, -1), 0, SAGE),
                ("LEFTPADDING", (0, 0), (-1, -1), 13),
                ("RIGHTPADDING", (0, 0), (-1, -1), 13),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]),
        )
    )

    warnings = [finding for finding in report.findings if finding.severity == "warning"]
    advice = [finding for finding in report.findings if finding.severity == "advice"]
    _add_findings(story, "Что стоит уточнить", warnings, styles)
    _add_findings(story, "Полезные идеи", advice, styles)
    _add_rewrites(story, report, styles)
    _add_questions(story, report, styles)

    if not warnings and not advice and not (report.review and (report.review.rewrites or report.review.questions)):
        story.append(Paragraph("Оставь в плане конкретные действия, понятный срок и небольшой запас времени.", styles["body"]))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Выбери из рекомендаций 1-2 пункта, которые реально попробовать на этой неделе.", styles["footer"]))
    document.build(
        story,
        onFirstPage=lambda canvas, doc: _draw_footer(canvas, doc, regular),
        onLaterPages=lambda canvas, doc: _draw_footer(canvas, doc, regular),
    )
    return buffer.getvalue()
