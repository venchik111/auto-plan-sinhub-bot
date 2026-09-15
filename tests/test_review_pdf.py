from io import BytesIO

from pypdf import PdfReader

from app.review.models import Finding, LlmReview, Rewrite, ReviewTask, StudentWeek
from app.review.pdf import build_student_recommendations_pdf
from app.review.service import StudentReport


def test_student_recommendations_pdf_contains_student_facing_sections() -> None:
    label = "14.09-20.09"
    week = StudentWeek(
        student="Анна Петрова",
        week_label=label,
        goals=("Подготовиться к зачёту",),
        goals_raw="Подготовиться к зачёту",
        tasks=(ReviewTask(1, "База", "Решить задачи", "Пн", ""),),
        reflection="",
    )
    review = LlmReview(
        findings=(Finding("llm", "vague_task", "warning", "task:1", "Добавь измеримый результат."),),
        rewrites=(Rewrite("task:1", "Решить задачи", "Решить 10 задач до среды"),),
        questions=("Как поймёшь, что задача выполнена?",),
        summary="Неделя станет понятнее, если добавить измеримый результат.",
        model="opencode/test",
    )
    report = StudentReport(
        student=week.student,
        week_label=label,
        status="discuss",
        llm_state="fresh",
        findings=review.findings,
        review=review,
        analyzed_at="2026-09-15T00:00:00+00:00",
        error=None,
        week=week,
    )

    pdf = build_student_recommendations_pdf(report)
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert pdf.startswith(b"%PDF-")
    assert len(reader.pages) == 1
    assert "Анна Петрова" in text
    assert "Рекомендации к плану недели" in text
    assert "КАК СДЕЛАТЬ ФОРМУЛИРОВКИ ЯСНЕЕ" in text
    assert "ВОПРОСЫ ДЛЯ САМОПРОВЕРКИ" in text
