from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any


SPHERES = ("База", "Профиль", "Коллектив", "Спорт", "Личное")
DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

_SPHERE_ALIASES = {
    "база": "База",
    "общеобразовательные предметы": "База",
    "профиль": "Профиль",
    "предметы направления": "Профиль",
    "коллектив": "Коллектив",
    "спорт": "Спорт",
    "личное": "Личное",
}
_DAY_ALIASES = {
    "пн": "Пн",
    "понедельник": "Пн",
    "вт": "Вт",
    "вторник": "Вт",
    "ср": "Ср",
    "среда": "Ср",
    "чт": "Чт",
    "четверг": "Чт",
    "пт": "Пт",
    "пятница": "Пт",
    "сб": "Сб",
    "суббота": "Сб",
    "вс": "Вс",
    "воскресенье": "Вс",
}


class PlanValidationError(ValueError):
    """The model returned a plan that cannot be safely written to the sheet."""


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_sphere(value: Any) -> str:
    normalized = _clean_text(value).lower()
    if normalized not in _SPHERE_ALIASES:
        raise PlanValidationError(
            f"Неизвестная сфера «{value}». Допустимы: {', '.join(SPHERES)}."
        )
    return _SPHERE_ALIASES[normalized]


def normalize_day(value: Any) -> str:
    normalized = _clean_text(value).lower().replace("ё", "е")
    if normalized not in _DAY_ALIASES:
        raise PlanValidationError(
            f"Неизвестный день «{value}». Допустимы: {', '.join(DAYS)}."
        )
    return _DAY_ALIASES[normalized]


@dataclass(frozen=True)
class Task:
    sphere: str
    text: str
    day: str
    time_minutes: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Task":
        text = _clean_text(raw.get("task", raw.get("text")))
        if not text:
            raise PlanValidationError("У одной из задач отсутствует текст.")
        if len(text) > 300:
            raise PlanValidationError("Текст задачи слишком длинный (больше 300 символов).")

        raw_time = raw.get("time_minutes")
        time_minutes: int | None = None
        if raw_time not in (None, "", 0):
            try:
                time_minutes = int(raw_time)
            except (TypeError, ValueError) as exc:
                raise PlanValidationError("Плановое время задачи должно быть числом минут.") from exc
            if not 1 <= time_minutes <= 1440:
                raise PlanValidationError("Плановое время задачи должно быть от 1 до 1440 минут.")

        return cls(
            sphere=normalize_sphere(raw.get("sphere")),
            text=text,
            day=normalize_day(raw.get("day")),
            time_minutes=time_minutes,
        )

    def display_text(self) -> str:
        if self.time_minutes is None:
            return self.text
        return f"{self.text} ({self.time_minutes} мин.)"


@dataclass(frozen=True)
class PlanDraft:
    week_label: str
    goals: tuple[str, ...]
    tasks: tuple[Task, ...]
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any], week_label: str) -> "PlanDraft":
        if not isinstance(raw, dict):
            raise PlanValidationError("LLM вернула не JSON-объект.")
        raw_goals = raw.get("goals", [])
        if isinstance(raw_goals, str):
            raw_goals = [line for line in raw_goals.splitlines() if line.strip()]
        if not isinstance(raw_goals, list):
            raise PlanValidationError("Поле goals должно быть списком целей.")
        goals = tuple(
            re.sub(r"^\d+[.)]\s*", "", _clean_text(goal))
            for goal in raw_goals
            if _clean_text(goal)
        )
        if not goals:
            raise PlanValidationError("LLM не выделила ни одной цели недели.")
        if len(goals) > 6:
            raise PlanValidationError("Целей получилось слишком много. Оставь не больше 6.")

        raw_tasks = raw.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlanValidationError("LLM не выделила ни одной задачи.")
        tasks = tuple(Task.from_dict(task) for task in raw_tasks if isinstance(task, dict))
        if not tasks:
            raise PlanValidationError("LLM не выделила ни одной корректной задачи.")

        raw_warnings = raw.get("warnings", [])
        if isinstance(raw_warnings, str):
            raw_warnings = [raw_warnings]
        warnings = tuple(_clean_text(warning) for warning in raw_warnings if _clean_text(warning))
        draft = cls(week_label=week_label, goals=goals, tasks=tasks, warnings=warnings)
        return draft.with_automatic_warnings()

    @classmethod
    def from_json(cls, value: str) -> "PlanDraft":
        raw = json.loads(value)
        if not isinstance(raw, dict) or "week_label" not in raw:
            raise PlanValidationError("Сохранённый черновик имеет неверный формат.")
        return cls.from_dict(raw, str(raw["week_label"]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_label": self.week_label,
            "goals": list(self.goals),
            "tasks": [asdict(task) for task in self.tasks],
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def with_automatic_warnings(self) -> "PlanDraft":
        warnings = list(self.warnings)
        by_day = Counter(task.day for task in self.tasks)
        for day, count in by_day.items():
            if count > 3:
                warnings.append(f"На {day} запланировано {count} задач. В методичке рекомендуется не больше 3.")
        if len({task.sphere for task in self.tasks}) > 4:
            warnings.append("Задействованы почти все сферы. Проверь, не распыляется ли план.")
        missing_time = sum(task.time_minutes is None for task in self.tasks)
        if missing_time:
            warnings.append("У части задач не указано плановое время. Его можно добавить правкой до подтверждения.")
        return PlanDraft(
            week_label=self.week_label,
            goals=self.goals,
            tasks=self.tasks,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def as_sheet_rows(self) -> list[list[str]]:
        goal_text = "\n".join(f"{index}. {goal}" for index, goal in enumerate(self.goals, 1))
        rows: list[list[str]] = []
        for index, task in enumerate(self.tasks):
            rows.append(
                [
                    self.week_label,
                    goal_text if index == 0 else "",
                    task.sphere,
                    task.display_text(),
                    task.day,
                    "⏳",
                    "",
                ]
            )
        return rows
