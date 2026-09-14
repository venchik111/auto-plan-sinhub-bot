from app.models import PlanDraft
from app.weeks import is_week_label, week_label


def test_week_label_is_compatible_with_template() -> None:
    from datetime import date

    assert week_label(date(2026, 9, 14)) == "14.09-20.09"
    assert week_label(date(2026, 9, 17)) == "14.09-20.09"


def test_plan_is_serialized_to_student_sheet_rows() -> None:
    draft = PlanDraft.from_dict(
        {
            "goals": ["Сдать реферат", "Сходить на тренировку"],
            "tasks": [
                {"sphere": "База", "task": "Закончить реферат", "day": "Пт", "time_minutes": 90},
                {"sphere": "Спорт", "task": "Тренировка", "day": "Сб", "time_minutes": None},
            ],
            "warnings": [],
        },
        "14.09-20.09",
    )
    assert draft.as_sheet_rows() == [
        ["14.09-20.09", "1. Сдать реферат\n2. Сходить на тренировку", "База", "Закончить реферат (90 мин.)", "Пт", "⏳", ""],
        ["14.09-20.09", "", "Спорт", "Тренировка", "Сб", "⏳", ""],
    ]


def test_week_label_parser() -> None:
    assert is_week_label("14.09-20.09")
    assert is_week_label("05.10-11.10")
    assert not is_week_label("следующая неделя")

