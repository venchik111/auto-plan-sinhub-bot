from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx


SKYENG_TIMETABLE_URL = "https://edu-avatar.skyeng.ru/api/v1/college-student-cabinet/timetable/weekly"


SUBJECT_LABELS = {
    "algorithms": "Алгоритмы",
    "career_track": "Карьерный трек",
    "english_in_profession": "Английский в профессии",
    "hardware_architecture": "Архитектура аппаратных средств",
    "is_design": "Проектирование и дизайн ИС",
    "is_development": "Разработка кода ИС",
    "math": "Математика",
    "operating_systems": "Операционные системы и среды",
    "physical_education": "Физкультура",
    "python": "Python",
    "soft_skills": "Soft skills",
}
ONLINE_TASK_TYPES = {"lesson", "practice", "trainer", "test", "planning"}
ONLINE_CALL_TYPES = {"live", "webinar"}
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
EVENT_TYPE_LABELS = {
    "lesson": "урок",
    "practice": "практика",
    "trainer": "тренажёр",
    "test": "тест",
    "planning": "планирование",
    "live": "live-событие",
    "webinar": "вебинар",
}


class SkyengError(RuntimeError):
    pass


class SkyengAuthError(SkyengError):
    pass


@dataclass(frozen=True)
class ScheduleEvent:
    date: str
    start: str
    end: str
    title: str
    event_type: str = "lesson"
    program: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _date_and_time(value: Any) -> tuple[str, str] | None:
    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", str(value or "")
    )
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    return f"{year}-{month}-{day}", f"{hour}:{minute}"


def _subject_label(value: Any) -> str:
    subject = str(value or "").strip()
    if not subject:
        return ""
    if subject in SUBJECT_LABELS:
        return SUBJECT_LABELS[subject]
    return subject.replace("_", " ").strip().capitalize()


def _nested_lesson_title(item: dict[str, Any]) -> str:
    context = item.get("context")
    if not isinstance(context, dict):
        return ""
    expected_lesson = context.get("expectedLesson")
    if not isinstance(expected_lesson, dict):
        return ""
    return str(expected_lesson.get("title") or "").strip()


def _event_from_item(
    item: dict[str, Any],
    start_key: str,
    end_key: str,
    fallback_title: str = "",
    fallback_type: str = "lesson",
    program: str = "",
) -> ScheduleEvent | None:
    start = _date_and_time(item.get(start_key))
    end = _date_and_time(item.get(end_key))
    if not start or not end:
        return None
    title = str(
        item.get("lessonTitle")
        or _nested_lesson_title(item)
        or item.get("title")
        or fallback_title
        or "Занятие"
    ).strip()
    event_type = str(item.get("type") or fallback_type).strip()
    return ScheduleEvent(
        date=start[0],
        start=start[1],
        end=end[1],
        title=title,
        event_type=event_type,
        program=program,
    )


def parse_weekly_response(payload: dict[str, Any]) -> list[ScheduleEvent]:
    events: list[ScheduleEvent] = []

    # Current Avatar schedule response.
    for day in payload.get("days", []) or []:
        if not isinstance(day, dict):
            continue
        for slot in day.get("slots", []) or []:
            if not isinstance(slot, dict):
                continue
            event = _event_from_item(
                slot,
                "startsAt",
                "endsAt",
                fallback_title=_subject_label(slot.get("subject")) or "Занятие",
                fallback_type=str(slot.get("activityType") or "lesson"),
                program=_subject_label(slot.get("subject")),
            )
            if event:
                events.append(event)

    # Legacy response shape kept for compatibility with older Avatar builds.
    for program in payload.get("programs", []) or []:
        if not isinstance(program, dict):
            continue
        program_name = str(
            program.get("programTitle") or program.get("title") or ""
        ).strip()
        stream_title = str(program.get("streamTitle") or "").strip()
        fallback_title = stream_title or program_name
        for task in program.get("streamTasks", []) or []:
            if not isinstance(task, dict):
                continue
            event = _event_from_item(
                task,
                "availableAt",
                "deadlineAt",
                fallback_title=fallback_title,
                program=program_name,
            )
            if event:
                events.append(event)

    for live in payload.get("lives", []) or []:
        if not isinstance(live, dict):
            continue
        event = _event_from_item(live, "startAt", "endAt")
        if event:
            events.append(event)

    unique: dict[tuple[str, str, str, str], ScheduleEvent] = {}
    for event in events:
        unique[(event.date, event.start, event.end, event.title)] = event
    return sorted(unique.values(), key=lambda event: (event.date, event.start, event.end, event.title))


def render_schedule(events: list[ScheduleEvent | dict[str, Any]]) -> str:
    normalized: list[ScheduleEvent] = []
    for item in events:
        if isinstance(item, ScheduleEvent):
            normalized.append(item)
        elif isinstance(item, dict):
            try:
                normalized.append(ScheduleEvent(**item))
            except TypeError:
                continue
    normalized.sort(key=lambda event: (event.date, event.start, event.end, event.title))
    lines = [
        "РАСПИСАНИЕ SKYENG — ОНЛАЙН-ПЛАТФОРМА",
        "Уроки, практики, тренажёры и тесты не нужно посещать как обычные пары: это активности, которые нужно выполнить на платформе.",
        "Время онлайн-активности — ориентир и длительность выполнения, а не занятый интервал. Вебинары и практика с наставником — это онлайн-созвоны внутри Skyeng с фиксированным временем подключения.",
    ]
    current_day = ""
    for event in normalized:
        if event.date != current_day:
            current_day = event.date
            day = date.fromisoformat(current_day)
            lines.append("")
            lines.append(f"{day.day:02d}.{day.month:02d} ({WEEKDAYS[day.weekday()]}):")
        subject = f"{event.program}: " if event.program and event.program != event.title else ""
        activity_label = EVENT_TYPE_LABELS.get(event.event_type, event.event_type or "активность")
        if event.event_type in ONLINE_TASK_TYPES:
            marker = f"{activity_label}; выполнить на платформе"
        elif event.event_type in ONLINE_CALL_TYPES:
            marker = f"{activity_label}; онлайн-созвон на платформе, учесть как фиксированное"
        else:
            marker = f"{activity_label}; учесть в плане"
        lines.append(f"- {event.start}–{event.end} — {subject}{event.title} [{marker}]")
    if not normalized:
        lines.append("\nНа эту неделю занятий не найдено.")
    return "\n".join(lines)


class SkyengScheduleClient:
    def __init__(self, storage_state_file: Path):
        self.storage_state_file = storage_state_file

    @property
    def is_connected(self) -> bool:
        return self.storage_state_file.is_file()

    def _cookies(self) -> httpx.Cookies:
        if not self.storage_state_file.is_file():
            raise SkyengAuthError(
                "Авторизация Skyeng не подключена. Запусти: "
                "python -m app.skyeng_login"
            )
        try:
            state = json.loads(self.storage_state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkyengAuthError("Не удалось прочитать сохранённую авторизацию Skyeng.") from exc
        cookies = httpx.Cookies()
        for item in state.get("cookies", []) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            domain = item.get("domain") or "api.skyeng.ru"
            if "skyeng.ru" not in domain:
                continue
            cookies.set(
                str(item["name"]),
                str(item.get("value", "")),
                domain=domain,
                path=str(item.get("path") or "/"),
            )
        return cookies

    async def fetch_week(self, start: date, end: date) -> list[ScheduleEvent]:
        cookies = self._cookies()
        try:
            async with httpx.AsyncClient(
                timeout=30,
                cookies=cookies,
                headers={"User-Agent": "AutoPlan/1.0"},
            ) as client:
                response = await client.get(
                    SKYENG_TIMETABLE_URL,
                    params={"start": start.isoformat(), "end": end.isoformat()},
                )
        except httpx.HTTPError as exc:
            raise SkyengError(f"Не удалось получить расписание Skyeng: {exc}") from exc
        if response.status_code in {401, 403}:
            raise SkyengAuthError(
                "Skyeng отклонил сохранённую авторизацию. Подключи её заново."
            )
        if response.is_error:
            raise SkyengError(f"Skyeng вернул HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SkyengError("Skyeng вернул не JSON.") from exc
        if not isinstance(payload, dict):
            raise SkyengError("Skyeng вернул неожиданный формат расписания.")
        events = parse_weekly_response(payload)
        start_text = start.isoformat()
        end_text = end.isoformat()
        return [event for event in events if start_text <= event.date <= end_text]
