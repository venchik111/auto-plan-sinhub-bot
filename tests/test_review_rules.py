from datetime import date

from app.review.models import ReviewTask, StudentWeek
from app.review.rules import evaluate

DURING = date(2026, 9, 16)  # неделя 14.09-20.09 идёт
AFTER = date(2026, 9, 22)   # неделя 14.09-20.09 закончилась


def task(index, sphere="База", text="Решить 10 задач", day="Пн", status=""):
    return ReviewTask(index=index, sphere=sphere, text=text, day=day, status=status)


def week(goals=("Цель 1", "Цель 2"), tasks=None, reflection="", label="14.09-20.09"):
    if tasks is None:
        tasks = (task(1, "База", day="Пн"), task(2, "Спорт", "Пробежка 30 минут", "Вт"))
    return StudentWeek("Студент", label, tuple(goals), "\n".join(goals), tuple(tasks), reflection)


def rules_of(findings):
    return [(f.rule, f.severity, f.target) for f in findings]


def test_good_plan_has_no_findings() -> None:
    assert evaluate(week(), None, DURING) == []


def test_no_plan_is_blocker_and_stops_other_rules() -> None:
    empty = StudentWeek.empty("Студент", "14.09-20.09")
    assert rules_of(evaluate(empty, None, AFTER)) == [("no_plan", "blocker", "week")]


def test_example_copy_is_blocker() -> None:
    example = week(goals=("Сдать реферат", "Сходить в зал"))
    copy = week(goals=("  сдать   реферат", "Сходить в зал"))
    assert rules_of(evaluate(copy, example, DURING)) == [("example_copy", "blocker", "week")]


def test_goals_count_boundaries() -> None:
    assert rules_of(evaluate(week(goals=("Одна",)), None, DURING)) == [("goals_count", "warning", "week")]
    assert evaluate(week(goals=("1", "2", "3", "4")), None, DURING) == []
    assert rules_of(evaluate(week(goals=("1", "2", "3", "4", "5")), None, DURING)) == [("goals_count", "warning", "week")]
    findings = evaluate(week(goals=()), None, DURING)
    assert findings[0].message == "Цели недели не заполнены."


def test_spheres_count() -> None:
    four = [task(1, "База"), task(2, "Профиль", day="Вт"), task(3, "Спорт", day="Ср"), task(4, "Личное", day="Чт")]
    assert rules_of(evaluate(week(tasks=four), None, DURING)) == [("spheres_count", "advice", "week")]
    five = four + [task(5, "Коллектив", day="Пт")]
    assert rules_of(evaluate(week(tasks=five), None, DURING)) == [("spheres_count", "warning", "week")]


def test_tasks_per_day() -> None:
    three = [task(1, "База", day="Пн"), task(2, "Спорт", day="Пн"), task(3, "База", day="Пн")]
    assert evaluate(week(tasks=three), None, DURING) == []
    four = three + [task(4, "Спорт", day="Пн")]
    assert rules_of(evaluate(week(tasks=four), None, DURING)) == [("tasks_per_day", "warning", "day:Пн")]


def test_missing_fields() -> None:
    tasks = [task(1, "База"), task(2, "", "", "", "")]
    findings = evaluate(week(tasks=tasks), None, DURING)
    assert rules_of(findings) == [("missing_fields", "warning", "task:2")]
    assert findings[0].message == "У задачи 2 не заполнено: сфера, текст задачи, день."


def test_no_balance_is_advice() -> None:
    academic = [task(1, "База"), task(2, "Профиль", day="Вт")]
    assert rules_of(evaluate(week(tasks=academic), None, DURING)) == [("no_balance", "advice", "week")]
    single = [task(1, "База")]
    assert evaluate(week(tasks=single), None, DURING) == []


def test_week_results_checked_only_after_week_end() -> None:
    tasks = [task(1, "База", status="✅"), task(2, "Спорт", day="Вт", status="")]
    assert evaluate(week(tasks=tasks), None, DURING) == []
    findings = evaluate(week(tasks=tasks), None, AFTER)
    assert rules_of(findings) == [
        ("statuses_missing", "warning", "week"),
        ("reflection_missing", "warning", "reflection"),
    ]
    assert findings[0].message == "Неделя закончилась, а у задач 2 не проставлен статус."


def test_reflection_present_after_week_end() -> None:
    tasks = [task(1, "База", status="✅"), task(2, "Спорт", day="Вт", status="❌")]
    assert evaluate(week(tasks=tasks, reflection="Помогло расписание"), None, AFTER) == []
