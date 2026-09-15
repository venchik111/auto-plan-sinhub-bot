from datetime import date

import pytest

from app.review.models import Finding, LlmReview, Rewrite, ReviewTask, StudentWeek
from app.review.sheet import FreshmenSheetReader, parse_student_rows, split_goals
from app.sheets import SheetsError
from app.weeks import canonical_week_label, week_end_from_label, week_has_ended

HEADER = ["Неделя", "Цели недели", "Сфера", "Задача", "День", "Статус", "Рефлексия"]


def test_canonical_week_label_normalizes_leading_zero_and_spaces() -> None:
    assert canonical_week_label(" 07.09-13.09 ") == "7.09-13.09"
    assert canonical_week_label("14.09 - 20.09") == "14.09-20.09"
    assert canonical_week_label("Неделя 7") is None


def test_week_end_uses_nearest_year() -> None:
    assert week_end_from_label("14.09-20.09", date(2026, 9, 15)) == date(2026, 9, 20)
    assert week_end_from_label("28.12-3.01", date(2027, 1, 2)) == date(2027, 1, 3)


def test_week_has_ended_only_after_sunday() -> None:
    assert not week_has_ended("14.09-20.09", date(2026, 9, 20))
    assert week_has_ended("14.09-20.09", date(2026, 9, 21))


def test_split_goals_by_lines_and_numbering() -> None:
    assert split_goals("1. Сдать реферат\n2) Сходить в зал\n\n- Лечь до 23:00") == (
        "Сдать реферат",
        "Сходить в зал",
        "Лечь до 23:00",
    )


def test_split_goals_inline_numbering() -> None:
    assert split_goals("1. Решить 15 задач 2. Сходить в зал") == ("Решить 15 задач", "Сходить в зал")


def test_split_goals_single_goal_keeps_numbers_inside() -> None:
    assert split_goals("Решить 15 задач по матанализу") == ("Решить 15 задач по матанализу",)


def test_parse_student_rows_groups_by_week() -> None:
    values = [
        HEADER,
        ["7.09-13.09", "1. Цель А\n2. Цель Б", "База", "Задача 1", "Пн", "✅", "Всё получилось"],
        ["7.09-13.09", "", "Спорт", "Задача 2", "Вт", "", ""],
        ["", "", "", "", "", "", ""],
        ["14.09-20.09", "1. Цель В", "Профиль", "Задача 3", "Ср"],
        ["без недели", "", "База", "Потерянная", "Чт", "", ""],
    ]
    weeks = parse_student_rows("Студент", values)

    assert set(weeks) == {"7.09-13.09", "14.09-20.09"}
    first = weeks["7.09-13.09"]
    assert first.goals == ("Цель А", "Цель Б")
    assert first.reflection == "Всё получилось"
    assert first.tasks == (
        ReviewTask(index=1, sphere="База", text="Задача 1", day="Пн", status="✅"),
        ReviewTask(index=2, sphere="Спорт", text="Задача 2", day="Вт", status=""),
    )
    assert weeks["14.09-20.09"].tasks[0].status == ""


def test_models_round_trip() -> None:
    review = LlmReview(
        findings=(Finding("llm", "measurable", "warning", "goal:1", "Неизмеримо"),),
        rewrites=(Rewrite("goal:1", "Подтянуть матан", "Решить 15 задач"),),
        questions=("Как поймёшь, что готово?",),
        summary="Кратко",
        model="opencode/model-a",
    )
    assert LlmReview.from_dict(review.to_dict()) == review
    assert StudentWeek.empty("С", "14.09-20.09").to_dict()["tasks"] == []


class FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.payload


class FakeValues:
    def __init__(self, by_title):
        self.by_title = by_title
        self.ranges = None

    def batchGet(self, spreadsheetId, ranges):
        self.ranges = ranges
        return FakeRequest(
            {"valueRanges": [{"values": self.by_title[r.split("'!")[0].strip("'").replace("''", "'")]} for r in ranges]}
        )


class FakeSpreadsheets:
    def __init__(self, by_title):
        self.by_title = by_title
        self.values_resource = FakeValues(by_title)

    def get(self, spreadsheetId, fields):
        return FakeRequest({"sheets": [{"properties": {"title": t}} for t in self.by_title]})

    def values(self):
        return self.values_resource


class FakeService:
    def __init__(self, by_title):
        self.resource = FakeSpreadsheets(by_title)

    def spreadsheets(self):
        return self.resource


def test_reader_skips_service_tabs_and_separates_example() -> None:
    row = ["14.09-20.09", "1. Цель", "База", "Задача", "Пн", "", ""]
    service = FakeService(
        {
            "Инструкция (куратор)": [["текст"]],
            "Дашборд куратора": [["№"]],
            "пример": [HEADER, row],
            "Студент О'Нил": [HEADER, row],
            "Пустой Студент": [HEADER],
        }
    )
    group = FreshmenSheetReader(service, "sid").read_group()

    assert sorted(group.students) == ["Пустой Студент", "Студент О'Нил"]
    assert "14.09-20.09" in group.example
    assert group.week_of("Пустой Студент", "14.09-20.09") == StudentWeek.empty("Пустой Студент", "14.09-20.09")
    assert "'Студент О''Нил'!A1:G500" in service.resource.values_resource.ranges


def test_reader_wraps_http_errors() -> None:
    from googleapiclient.errors import HttpError

    class Resp(dict):
        status = 403
        reason = "Forbidden"

    class BrokenSpreadsheets(FakeSpreadsheets):
        def get(self, spreadsheetId, fields):
            return FakeRequest(error=HttpError(Resp(), b"denied"))

    service = FakeService({})
    service.resource = BrokenSpreadsheets({})
    with pytest.raises(SheetsError, match="HTTP 403"):
        FreshmenSheetReader(service, "sid").read_group()
