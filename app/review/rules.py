from __future__ import annotations

from collections import Counter
from datetime import date

from ..weeks import week_has_ended
from .models import ACADEMIC_SPHERES, NON_ACADEMIC_SPHERES, Finding, StudentWeek
from .sheet import normalize_text


def _finding(rule: str, severity: str, target: str, message: str) -> Finding:
    return Finding(source="rules", rule=rule, severity=severity, target=target, message=message)


def _fingerprint(week: StudentWeek) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        tuple(normalize_text(goal) for goal in week.goals),
        tuple(normalize_text(task.text) for task in week.tasks),
    )


def evaluate(week: StudentWeek, example: StudentWeek | None, today: date) -> list[Finding]:
    if not week.goals_raw and not week.tasks:
        return [_finding("no_plan", "blocker", "week", "Студент ещё не заполнил план на эту неделю.")]
    if example is not None and _fingerprint(week) == _fingerprint(example):
        return [
            _finding(
                "example_copy",
                "blocker",
                "week",
                "План совпадает с листом «пример» — студент не заполнил свою неделю.",
            )
        ]

    findings: list[Finding] = []
    findings.extend(_goals_count(week))
    findings.extend(_spheres_count(week))
    findings.extend(_tasks_per_day(week))
    findings.extend(_missing_fields(week))
    findings.extend(_no_balance(week))
    if week_has_ended(week.week_label, today):
        findings.extend(_week_results(week))
    return findings


def _goals_count(week: StudentWeek) -> list[Finding]:
    count = len(week.goals)
    if count == 0:
        return [_finding("goals_count", "warning", "week", "Цели недели не заполнены.")]
    if count < 2 or count > 4:
        return [
            _finding(
                "goals_count",
                "warning",
                "week",
                f"Целей недели {count}, а рекомендуется 2–4.",
            )
        ]
    return []


def _spheres_count(week: StudentWeek) -> list[Finding]:
    count = len({normalize_text(task.sphere) for task in week.tasks if task.sphere})
    if count > 4:
        return [
            _finding(
                "spheres_count",
                "warning",
                "week",
                f"Задействовано сфер: {count}. Рекомендуется 2–3, максимум 3–4.",
            )
        ]
    if count == 4:
        return [
            _finding(
                "spheres_count",
                "advice",
                "week",
                "Задействовано 4 сферы — это верхняя граница, стоит обсудить приоритеты.",
            )
        ]
    return []


def _tasks_per_day(week: StudentWeek) -> list[Finding]:
    by_day = Counter(task.day for task in week.tasks if task.day)
    return [
        _finding(
            "tasks_per_day",
            "warning",
            f"day:{day}",
            f"На {day} запланировано задач: {count}. Рекомендуется не больше 3.",
        )
        for day, count in by_day.items()
        if count > 3
    ]


def _missing_fields(week: StudentWeek) -> list[Finding]:
    findings = []
    for task in week.tasks:
        missing = [
            name
            for name, value in (("сфера", task.sphere), ("текст задачи", task.text), ("день", task.day))
            if not value
        ]
        if missing:
            findings.append(
                _finding(
                    "missing_fields",
                    "warning",
                    f"task:{task.index}",
                    f"У задачи {task.index} не заполнено: {', '.join(missing)}.",
                )
            )
    return findings


def _no_balance(week: StudentWeek) -> list[Finding]:
    spheres = [normalize_text(task.sphere) for task in week.tasks if task.sphere]
    if len(spheres) < 2:
        return []
    has_academic = any(sphere in ACADEMIC_SPHERES for sphere in spheres)
    has_other = any(sphere in NON_ACADEMIC_SPHERES for sphere in spheres)
    if has_academic and not has_other:
        message = "Все задачи академические (База/Профиль) — нет задач на спорт, коллектив или личное."
    elif has_other and not has_academic:
        message = "Нет академических задач (База/Профиль)."
    else:
        return []
    return [_finding("no_balance", "advice", "week", message)]


def _week_results(week: StudentWeek) -> list[Finding]:
    findings = []
    without_status = [str(task.index) for task in week.tasks if not task.status]
    if without_status:
        findings.append(
            _finding(
                "statuses_missing",
                "warning",
                "week",
                f"Неделя закончилась, а у задач {', '.join(without_status)} не проставлен статус.",
            )
        )
    if not week.reflection.strip():
        findings.append(
            _finding("reflection_missing", "warning", "reflection", "Неделя закончилась, а рефлексия не заполнена.")
        )
    return findings
