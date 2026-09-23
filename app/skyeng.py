from __future__ import annotations

import html
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx


SKYENG_TIMETABLE_URL = "https://edu-avatar.skyeng.ru/api/v1/college-student-cabinet/timetable/weekly"
SKYENG_AUTH_START_URL = "https://edu-avatar.skyeng.ru/auth-external/connect/central-university/login"
SKYENG_RETURN_URL = "https://avatar.skyeng.ru/student/schedule"


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
    "practice": "домашка",
    "trainer": "подготовка к тесту в тренажере",
    "test": "тест",
    "planning": "планирование",
    "live": "вебинар",
    "webinar": "вебинар",
}
EVENT_TIME_HINTS = {
    "lesson": "ориентир 20 мин",
    "practice": "не больше 60 мин",
    "trainer": "ориентир 60 мин",
}


class SkyengError(RuntimeError):
    pass


class SkyengAuthError(SkyengError):
    pass


def parse_skyeng_cookie_input(value: str) -> dict[str, Any]:
    """Convert a Cookie header or exported browser JSON to storage-state JSON."""
    source = value.strip()
    if not source:
        raise SkyengAuthError("Вставь Cookie из запроса расписания Skyeng.")

    cookies: list[dict[str, Any]] = []
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        raw_cookies = parsed.get("cookies", [])
    elif isinstance(parsed, list):
        raw_cookies = parsed
    else:
        raw_cookies = None

    if isinstance(raw_cookies, list):
        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            cookie_value = str(item.get("value") or "")
            domain = str(item.get("domain") or ".skyeng.ru").strip()
            if not name or "skyeng.ru" not in domain.lower():
                continue
            cookies.append(
                {
                    "name": name,
                    "value": cookie_value,
                    "domain": domain,
                    "path": str(item.get("path") or "/"),
                }
            )
    else:
        if source.lower().startswith("cookie:"):
            source = source.split(":", 1)[1].strip()
        for part in source.split(";"):
            name, separator, cookie_value = part.strip().partition("=")
            if not separator or not name or any(char.isspace() for char in name):
                continue
            cookies.append(
                {
                    "name": name,
                    "value": cookie_value,
                    "domain": ".skyeng.ru",
                    "path": "/",
                }
            )

    unique = {item["name"]: item for item in cookies}
    if not unique:
        raise SkyengAuthError(
            "Не удалось найти cookies Skyeng. Скопируй целиком значение заголовка Cookie."
        )
    return {"cookies": list(unique.values()), "origins": []}


def save_skyeng_cookie_state(path: Path, value: str) -> None:
    save_skyeng_state(path, parse_skyeng_cookie_input(value))


def save_skyeng_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _login_action(page: str) -> str | None:
    match = re.search(
        r'window\.authConfiguration\.urls\.loginAction\s*=\s*"([^"]+)"',
        page,
    )
    return html.unescape(match.group(1)) if match else None


def _is_captcha_challenge(response: httpx.Response) -> bool:
    """Detect the anti-bot page returned instead of the Skyeng login form."""
    url = str(response.url).lower()
    page = response.text.lower()
    return (
        "showcaptcha" in url
        or "smartcaptcha" in page
        or "вы не робот" in page
    )


def _skyeng_cookie_state(client: httpx.AsyncClient) -> dict[str, Any]:
    cookies = []
    for cookie in client.cookies.jar:
        domain = str(cookie.domain or "")
        if "skyeng.ru" not in domain.lower():
            continue
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": domain or ".skyeng.ru",
                "path": cookie.path or "/",
            }
        )
    unique = {item["name"]: item for item in cookies}
    if not unique:
        raise SkyengAuthError("Skyeng не вернул сессию после входа.")
    return {"cookies": list(unique.values()), "origins": []}


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
        "Словарь: платформа Skyeng = ЛК; practice по уроку = домашка; trainer = подготовка к тесту в тренажере.",
        "Уроки, домашки, тренажеры и тесты не нужно посещать как обычные пары: это активности, которые нужно выполнить в ЛК.",
        "Ориентиры времени: урок в ЛК — 15–20 минут, домашка — не больше 60 минут, подготовка к тесту в тренажере — около 60 минут. Вебинары и практика с наставником — вебинары внутри Skyeng с фиксированным временем.",
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
            marker = f"{activity_label}; выполнить в ЛК"
            time_hint = EVENT_TIME_HINTS.get(event.event_type)
            if time_hint:
                marker += f"; {time_hint}"
        elif event.event_type in ONLINE_CALL_TYPES:
            marker = f"{activity_label}; учесть как фиксированное время"
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
                "Авторизация Skyeng не подключена. Вставь Cookie через форму на сайте."
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

    @staticmethod
    async def login_with_password(login: str, password: str) -> dict[str, Any]:
        """Complete Skyeng's external login flow and return only its session cookies."""
        login = login.strip()
        if not login or not password:
            raise SkyengAuthError("Укажи логин или email и пароль Skyeng.")
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "AutoPlan/1.0"},
            ) as client:
                response = await client.get(
                    SKYENG_AUTH_START_URL,
                    params={"returnUrl": SKYENG_RETURN_URL},
                )
                action = _login_action(response.text)
                if not action:
                    raise SkyengAuthError("Не удалось открыть форму входа Skyeng.")

                response = await client.post(action, data={"username": login})
                if _is_captcha_challenge(response):
                    raise SkyengAuthError(
                        "Skyeng включил проверку «Я не робот». Вход паролем через сервер "
                        "сейчас недоступен — войди в Skyeng в браузере и подключи расписание "
                        "через Cookie."
                    )
                action = _login_action(response.text)
                if not action:
                    raise SkyengAuthError(
                        "Skyeng не открыл следующий шаг входа. Используй подключение через Cookie."
                    )
                if not re.search(r'name=["\']password["\']|activePage\s*=\s*["\']password', response.text, re.I):
                    raise SkyengAuthError(
                        "Skyeng запросил код подтверждения. Используй вход на Skyeng и вставь Cookie."
                    )

                response = await client.post(
                    action,
                    data={"username": login, "password": password, "credentialId": ""},
                )
                if response.status_code >= 400:
                    raise SkyengAuthError("Skyeng отклонил логин или пароль.")
                if _login_action(response.text):
                    raise SkyengAuthError("Skyeng отклонил логин или пароль.")
                return _skyeng_cookie_state(client)
        except SkyengAuthError:
            raise
        except httpx.HTTPError as exc:
            raise SkyengError("Не удалось связаться со Skyeng для входа.") from exc

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
