import asyncio
from datetime import date

import pytest

from app.database import Database
from app.opencode_cli import LLMError
from app.review.models import Finding, LlmReview, ReviewTask, StudentWeek
from app.review.service import ReviewService, compute_status, data_hash
from app.review.sheet import Group

LABEL = "14.09-20.09"
TODAY = date(2026, 9, 16)


def make_week(student, goals=("Цель 1", "Цель 2"), text="Решить 10 задач"):
    tasks = (ReviewTask(1, "База", text, "Пн", ""), ReviewTask(2, "Спорт", "Пробежка", "Вт", ""))
    return StudentWeek(student, LABEL, goals, "\n".join(goals), tasks, "")


class FakeReader:
    def __init__(self, group):
        self.group = group

    def read_group(self):
        return self.group


class FakeAnalyzer:
    def __init__(self, findings=(), error=None):
        self.findings = findings
        self.error = error
        self.calls = []

    async def analyze(self, week, formal, week_ended):
        self.calls.append((week.student, week_ended))
        if self.error:
            raise self.error
        return LlmReview(tuple(self.findings), (), ("Вопрос?",), "Сводка", "opencode/model-a")


def warning(n):
    return Finding("llm", "vague_task", "warning", f"task:{n}", "Размыто")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "db.sqlite3")
    database.init()
    return database


def service_with(db, analyzer, students):
    group = Group(students=students, example={})
    return ReviewService(FakeReader(group), analyzer, db, lambda: TODAY)


def test_compute_status() -> None:
    blocker = Finding("rules", "no_plan", "blocker", "week", "Пусто")
    advice = Finding("rules", "no_balance", "advice", "week", "Совет")
    assert compute_status([blocker, warning(1)]) == "empty"
    assert compute_status([warning(1), warning(2), warning(3)]) == "discuss"
    assert compute_status([warning(1), advice]) == "remarks"
    assert compute_status([advice]) == "ok"


def test_data_hash_changes_with_content() -> None:
    assert data_hash(make_week("А")) == data_hash(make_week("А"))
    assert data_hash(make_week("А")) != data_hash(make_week("А", text="Другое"))


def test_overview_without_llm_and_sorting(db) -> None:
    students = {
        "Бета": {LABEL: make_week("Бета", goals=("Одна",))},
        "Альфа": {LABEL: make_week("Альфа")},
        "Пустой": {},
    }
    reports = service_with(db, FakeAnalyzer(), students).group_overview(LABEL)
    assert [(r.student, r.status, r.llm_state) for r in reports] == [
        ("Бета", "remarks", "none"),
        ("Альфа", "ok", "none"),
        ("Пустой", "empty", "none"),
    ]
    assert reports[0].summary_dict() == {
        "student": "Бета",
        "status": "remarks",
        "llm_state": "none",
        "warnings": 1,
        "advice": 0,
    }


def test_analyze_student_saves_review_and_updates_status(db) -> None:
    analyzer = FakeAnalyzer(findings=[warning(1), warning(2), warning(1)])
    service = service_with(db, analyzer, {"Альфа": {LABEL: make_week("Альфа")}})

    asyncio.run(service.analyze_student("Альфа", LABEL))
    report = service.student_report("Альфа", LABEL)

    assert analyzer.calls == [("Альфа", False)]
    assert (report.status, report.llm_state) == ("discuss", "fresh")
    assert report.review.summary == "Сводка"
    full = report.to_dict()
    assert full["plan"]["goals"] == ["Цель 1", "Цель 2"]
    assert full["questions"] == ["Вопрос?"]
    assert full["model"] == "opencode/model-a"


def test_changed_sheet_data_marks_review_stale(db) -> None:
    students = {"Альфа": {LABEL: make_week("Альфа")}}
    service = service_with(db, FakeAnalyzer(), students)
    asyncio.run(service.analyze_student("Альфа", LABEL))

    students["Альфа"][LABEL] = make_week("Альфа", text="Изменённая задача")

    assert service.student_report("Альфа", LABEL).llm_state == "stale"
    assert service.pending_students(LABEL) == ["Альфа"]


def test_failed_analysis_keeps_previous_review(db) -> None:
    students = {"Альфа": {LABEL: make_week("Альфа")}}
    asyncio.run(service_with(db, FakeAnalyzer(), students).analyze_student("Альфа", LABEL))

    failing = service_with(db, FakeAnalyzer(error=LLMError("таймаут")), students)
    with pytest.raises(LLMError):
        asyncio.run(failing.analyze_student("Альфа", LABEL))
    report = failing.student_report("Альфа", LABEL)

    assert report.llm_state == "error"
    assert report.error == "таймаут"
    assert report.review is not None


def test_blocked_student_is_not_analyzed_or_pending(db) -> None:
    analyzer = FakeAnalyzer()
    service = service_with(db, analyzer, {"Пустой": {}, "Альфа": {LABEL: make_week("Альфа")}})

    asyncio.run(service.analyze_student("Пустой", LABEL))

    assert analyzer.calls == []
    assert service.pending_students(LABEL) == ["Альфа"]


def test_unknown_student_raises_key_error(db) -> None:
    service = service_with(db, FakeAnalyzer(), {})
    with pytest.raises(KeyError):
        service.student_report("Никто", LABEL)
    with pytest.raises(KeyError):
        asyncio.run(service.analyze_student("Никто", LABEL))


def test_allowed_students_filter_overview_and_access(db) -> None:
    students = {
        "Альфа": {LABEL: make_week("Альфа")},
        "Бета": {LABEL: make_week("Бета")},
        "Пустой": {},
    }
    service = ReviewService(
        FakeReader(Group(students=students, example={})),
        FakeAnalyzer(),
        db,
        lambda: TODAY,
        allowed_students=("Альфа", "Пустой"),
    )

    assert service.students() == ["Альфа", "Пустой"]
    assert [report.student for report in service.group_overview(LABEL)] == ["Альфа", "Пустой"]
    with pytest.raises(KeyError):
        service.student_report("Бета", LABEL)
    with pytest.raises(KeyError):
        asyncio.run(service.analyze_student("Бета", LABEL))
