from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import hash_password, new_session_token, normalize_login, verify_password
from .config import Settings
from .curator_web import create_curator_router
from .database import Database
from .llm import LLMError, OpenAICompatibleLLM
from .models import DAYS, SPHERES, PlanDraft, PlanValidationError
from .opencode_cli import OpenCodeRunner
from .review.analyzer import ReviewAnalyzer
from .review.queue import ReviewQueue
from .review.service import ReviewService
from .review.sheet import FreshmenSheetReader
from .skyeng import (
    SkyengAuthError,
    SkyengError,
    SkyengScheduleClient,
    render_schedule,
    save_skyeng_cookie_state,
    save_skyeng_state,
)
from .sheets import SheetsError, SheetsRepository
from .weeks import is_week_label, local_today, week_label, week_start as get_week_start


BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
AUTH_COOKIE = "auto_plan_auth"
ADMIN_COOKIE = "auto_plan_admin"
SESSION_PATTERN = re.compile(r"^[0-9a-f]{32}$")
AUTH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,}$")
SCHEDULE_CACHE_VERSION = 2
GUIDING_QUESTION_LABELS = {
    "goal": "Главный результат недели",
    "fixed": "Обязательные дела и события",
    "capacity": "Доступное время и силы",
    "carry_over": "Что важно не забыть или перенести",
}

settings = Settings.from_env()
database = Database(settings.database_path)
database.init()
sheets = SheetsRepository.from_settings(settings)
llm = OpenAICompatibleLLM(settings)

app = FastAPI(title="Авто-планирование", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

# The curator review section is optional: it needs its own spreadsheet and a
# password, otherwise the student site runs exactly as before.
if settings.freshmen_spreadsheet_id and settings.curator_password:
    review_service = ReviewService(
        FreshmenSheetReader.from_settings(settings),
        ReviewAnalyzer(OpenCodeRunner.from_settings(settings, settings.opencode_review_agent)),
        database,
        lambda: local_today(settings.app_timezone),
        settings.curator_students or None,
    )
    review_queue = ReviewQueue(review_service.analyze_student)
    app.include_router(
        create_curator_router(review_service, review_queue, settings.curator_password, WEB_DIR)
    )


class AccountRegisterRequest(BaseModel):
    login: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(min_length=2, max_length=150)


class AccountLoginRequest(BaseModel):
    login: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class ReflectionRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    draft: dict[str, Any]
    notes: str = Field(default="", max_length=4000)


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class GenerateRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    week_start: date | None = None
    source_text: str = Field(default="", max_length=8000)
    guiding_answers: dict[str, str] = Field(default_factory=dict)
    include_schedule: bool = True


class UpdateRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    week_start: date | None = None
    draft: dict[str, Any]
    correction: str = Field(min_length=3, max_length=4000)
    include_schedule: bool = True


class ConfirmRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    draft: dict[str, Any]


class ScheduleSyncRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    week_start: date | None = None


class SkyengConnectRequest(BaseModel):
    login: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=128)
    cookies: str = Field(default="", max_length=30000)
    week_start: date | None = None


class CarryOverRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    week_start: date | None = None


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": user["account_id"],
        "login": user["login"],
        "display_name": user["display_name"],
        "sheet_name": user["sheet_name"],
        "profile": user.get("profile") or {},
    }


def _skyeng_storage_path(storage_key: str) -> Path:
    directory = settings.skyeng_storage_state_file.parent / "skyeng"
    safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", storage_key)
    return directory / f"{safe_key}.json"


def _account_storage_key(session_id: str, user: dict[str, Any] | None = None) -> str:
    if user and user.get("account_id"):
        return f"account-{user['account_id']}"
    return f"session-{session_id}"


def _skyeng_client(
    session_id: str, user: dict[str, Any] | None = None
) -> SkyengScheduleClient:
    return SkyengScheduleClient(_skyeng_storage_path(_account_storage_key(session_id, user)))


def _new_session_id() -> str:
    return uuid.uuid4().hex


def _session(request: Request) -> tuple[str, dict[str, Any] | None, bool]:
    session_id = request.cookies.get(AUTH_COOKIE, "")
    if AUTH_TOKEN_PATTERN.fullmatch(session_id):
        user = database.get_account_by_session(session_id)
        if user:
            return session_id, user, False
    return _new_session_id(), None, True


def _require_user(request: Request) -> tuple[str, dict[str, Any]]:
    session_id, user, _ = _session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Сначала войди в одобренный аккаунт.")
    return session_id, user


def _admin_token() -> str:
    return hmac.new(
        settings.admin_password.encode("utf-8"),
        b"auto-plan-admin-session",
        hashlib.sha256,
    ).hexdigest()


def _require_admin(request: Request) -> None:
    token = request.cookies.get(ADMIN_COOKIE, "")
    if not settings.admin_password or not hmac.compare_digest(token, _admin_token()):
        raise HTTPException(status_code=401, detail="Нужен вход администратора.")


def _validate_week(value: str) -> str:
    value = value.strip()
    if not is_week_label(value):
        raise HTTPException(status_code=400, detail="Укажи неделю в формате 14.09-20.09.")
    return value


def _validate_week_start(value: str, selected_start: date | None) -> date | None:
    """Validate the calendar date sent by the week picker.

    The sheet keeps the compact label ``dd.mm-dd.mm`` for compatibility with
    the existing template, while the ISO start date disambiguates the year
    when a user selects a week in the calendar.
    """
    if selected_start is None:
        return None
    if selected_start.weekday() != 0:
        raise HTTPException(status_code=400, detail="Неделя должна начинаться с понедельника.")
    if week_label(selected_start) != value:
        raise HTTPException(status_code=400, detail="Дата начала не совпадает с выбранной неделей.")
    return selected_start


def _has_planning_details(value: str) -> bool:
    details = re.sub(
        r"^(Главный результат|Фиксированные дела|Ресурс и время|Не забыть):?\s*$",
        "",
        value.strip(),
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return len(" ".join(details.split())) >= 10


def _compose_planning_text(source_text: str, guiding_answers: dict[str, str]) -> str:
    answers = []
    for key, label in GUIDING_QUESTION_LABELS.items():
        value = " ".join(str(guiding_answers.get(key, "") or "").split())
        if value:
            answers.append(f"- {label}: {value}")
    if not answers:
        return source_text.strip()
    answers_text = "НАВОДЯЩИЕ ВОПРОСЫ И ОТВЕТЫ:\n" + "\n".join(answers)
    source = source_text.strip()
    return f"{answers_text}\n\nОПИСАНИЕ ПЛАНОВ:\n{source}" if source else answers_text


def _week_dates(value: str, selected_start: date | None = None) -> tuple[date, date]:
    if selected_start is not None:
        return selected_start, selected_start + timedelta(days=6)
    start_text = value.split("-", 1)[0]
    day, month = (int(part) for part in start_text.split("."))
    today = local_today(settings.app_timezone)
    candidates: list[date] = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        raise HTTPException(status_code=400, detail="Не удалось определить дату недели.")
    start = min(candidates, key=lambda candidate: abs((candidate - today).days))
    return start, start + timedelta(days=6)


def _cache_is_fresh(cached: dict[str, Any] | None) -> bool:
    if not cached:
        return False
    if cached.get("parser_version") != SCHEDULE_CACHE_VERSION:
        return False
    try:
        fetched_at = datetime.fromisoformat(str(cached["fetched_at"]))
    except (KeyError, TypeError, ValueError):
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at < timedelta(days=7)


async def _load_schedule(
    session_id: str,
    week: str,
    force: bool = False,
    selected_start: date | None = None,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skyeng = _skyeng_client(session_id, user)
    # The sheet label intentionally has no year (for compatibility with the
    # template), so use the ISO Monday as the cache key whenever the calendar
    # supplied an exact week. Otherwise 28.12-03.01 from different years
    # could overwrite each other.
    cache_key = selected_start.isoformat() if selected_start is not None else week
    storage_key = _account_storage_key(session_id, user)
    cached = database.get_web_schedule(storage_key, cache_key)
    if not force and _cache_is_fresh(cached):
        return {
            "events": cached.get("events", []),
            "fetched_at": cached.get("fetched_at"),
            "connected": skyeng.is_connected,
            "stale": False,
        }
    if not skyeng.is_connected:
        return {
            "events": cached.get("events", []) if cached else [],
            "fetched_at": cached.get("fetched_at") if cached else None,
            "connected": False,
            "stale": bool(cached),
            "error": "Авторизация Skyeng ещё не подключена.",
        }

    start, end = _week_dates(week, selected_start)
    try:
        events = await skyeng.fetch_week(start, end)
    except SkyengAuthError:
        if cached:
            return {
                "events": cached.get("events", []),
                "fetched_at": cached.get("fetched_at"),
                "connected": False,
                "stale": True,
                "error": "Авторизация Skyeng устарела.",
            }
        raise
    except SkyengError:
        if cached:
            return {
                "events": cached.get("events", []),
                "fetched_at": cached.get("fetched_at"),
                "connected": True,
                "stale": True,
                "error": "Не удалось обновить расписание, использую последнюю копию.",
            }
        raise

    fetched_at = datetime.now(timezone.utc).isoformat()
    event_payload = [event.to_dict() for event in events]
    database.save_web_schedule(storage_key, cache_key, event_payload, fetched_at)
    return {
        "events": event_payload,
        "fetched_at": fetched_at,
        "connected": True,
        "stale": False,
    }


async def _planning_source(
    session_id: str,
    week: str,
    user_text: str,
    include_schedule: bool = True,
    selected_start: date | None = None,
    user: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    skyeng = _skyeng_client(session_id, user)
    if not include_schedule:
        return user_text, {
            "events": [],
            "connected": skyeng.is_connected,
            "stale": False,
            "included": False,
        }
    try:
        schedule = await _load_schedule(
            session_id, week, selected_start=selected_start, user=user
        )
    except SkyengError as exc:
        # Планирование не должно ломаться из-за временной недоступности
        # расписания: в этом случае LLM продолжит работать только с текстом
        # пользователя, а интерфейс покажет причину отдельно.
        return user_text, {
            "events": [],
            "connected": skyeng.is_connected,
            "stale": True,
            "error": str(exc),
        }
    events = schedule.get("events", [])
    if not events:
        return user_text, schedule
    schedule["included"] = True
    return f"{render_schedule(events)}\n\nМОИ ПЛАНЫ:\n{user_text}", schedule


def _draft_from_payload(payload: dict[str, Any], week: str) -> PlanDraft:
    try:
        return PlanDraft.from_dict(payload, week)
    except (PlanValidationError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/skyeng/connect")
async def connect_skyeng(
    request: Request, data: SkyengConnectRequest
) -> dict[str, str | bool | int]:
    session_id, user = _require_user(request)
    target = _skyeng_storage_path(_account_storage_key(session_id, user))
    temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        if data.login.strip() or data.password:
            if not data.login.strip() or not data.password:
                raise SkyengAuthError("Укажи логин или email и пароль Skyeng.")
            state = await SkyengScheduleClient.login_with_password(
                data.login, data.password
            )
            save_skyeng_state(temporary, state)
        elif data.cookies.strip():
            save_skyeng_cookie_state(temporary, data.cookies)
        else:
            raise SkyengAuthError("Укажи логин и пароль или вставь Cookie Skyeng.")
        selected = data.week_start or local_today(settings.app_timezone)
        start = selected - timedelta(days=selected.weekday())
        events = await SkyengScheduleClient(temporary).fetch_week(
            start, start + timedelta(days=6)
        )
        temporary.replace(target)
    except SkyengAuthError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, SkyengError) as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "connected": True,
        "status": "connected",
        "events": len(events),
    }


@app.get("/api/skyeng/status")
async def skyeng_status(request: Request) -> dict[str, str | bool]:
    session_id, user = _require_user(request)
    skyeng = _skyeng_client(session_id, user)
    if skyeng.is_connected:
        return {"connected": True, "status": "connected"}
    return {"connected": False, "status": "idle", "error": ""}


@app.get("/api/bootstrap")
async def bootstrap(request: Request) -> JSONResponse:
    _, user, _ = _session(request)
    today = local_today(settings.app_timezone)
    return JSONResponse(
        {
            "user": _public_user(user) if user else None,
            "current_week": week_label(today),
            "next_week": week_label(today, 1),
            "current_week_start": get_week_start(today).isoformat(),
            "next_week_start": get_week_start(today, 1).isoformat(),
            "spheres": list(SPHERES),
            "days": list(DAYS),
        }
    )


@app.post("/api/auth/register")
async def register_account(data: AccountRegisterRequest) -> dict[str, str]:
    login = normalize_login(data.login)
    if not re.fullmatch(r"[a-zа-яё0-9_.-]{3,50}", login):
        raise HTTPException(
            status_code=400,
            detail="Логин: от 3 до 50 символов, только буквы, цифры, точка, дефис или _.",
        )
    if database.get_account_by_login(login):
        raise HTTPException(status_code=409, detail="Такой логин уже занят.")
    try:
        student = sheets.find_student(data.display_name.strip())
    except SheetsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not student:
        raise HTTPException(
            status_code=404,
            detail="Не нашёл вкладку с таким названием. Скопируй ФИО из заголовка листа.",
        )
    try:
        database.create_account(
            login,
            hash_password(data.password),
            student.display_name,
            student.sheet_name,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Такой логин уже занят.") from exc
    return {"status": "pending", "message": "Заявка отправлена администратору."}


@app.post("/api/auth/login")
async def login_account(data: AccountLoginRequest) -> JSONResponse:
    login = normalize_login(data.login)
    account = database.get_account_by_login(login)
    if not account or not verify_password(data.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")
    if account["status"] == "pending":
        raise HTTPException(status_code=403, detail="Заявка ещё не одобрена администратором.")
    if account["status"] != "approved":
        raise HTTPException(status_code=403, detail="Заявка на регистрацию отклонена.")
    session_id = new_session_token()
    database.create_account_session(session_id, account["account_id"])
    response = JSONResponse({"user": _public_user(account)})
    response.set_cookie(
        AUTH_COOKIE,
        session_id,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/auth/logout")
async def logout_account(request: Request) -> JSONResponse:
    session_id = request.cookies.get(AUTH_COOKIE, "")
    if AUTH_TOKEN_PATTERN.fullmatch(session_id):
        database.delete_account_session(session_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE)
    return response


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(WEB_DIR / "admin.html")


@app.post("/api/admin/login")
async def admin_login(data: AdminLoginRequest) -> JSONResponse:
    if not settings.admin_password or not hmac.compare_digest(data.password, settings.admin_password):
        raise HTTPException(status_code=401, detail="Неверный пароль администратора.")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        ADMIN_COOKIE,
        _admin_token(),
        max_age=60 * 60 * 8,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/admin/logout")
async def admin_logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_COOKIE)
    return response


@app.get("/api/admin/accounts")
async def admin_accounts(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return {"accounts": [_admin_account(account) for account in database.list_accounts()]}


def _admin_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": account["account_id"],
        "login": account["login"],
        "display_name": account["display_name"],
        "sheet_name": account["sheet_name"],
        "status": account["status"],
        "created_at": account["created_at"],
        "approved_at": account.get("approved_at"),
    }


@app.post("/api/admin/accounts/{account_id}/approve")
async def approve_account(request: Request, account_id: int) -> dict[str, Any]:
    _require_admin(request)
    if not database.set_account_status(account_id, "approved"):
        raise HTTPException(status_code=404, detail="Аккаунт не найден.")
    return {"account": _admin_account(database.get_account(account_id) or {})}


@app.post("/api/admin/accounts/{account_id}/reject")
async def reject_account(request: Request, account_id: int) -> dict[str, Any]:
    _require_admin(request)
    if not database.set_account_status(account_id, "rejected"):
        raise HTTPException(status_code=404, detail="Аккаунт не найден.")
    return {"account": _admin_account(database.get_account(account_id) or {})}


@app.get("/api/schedule")
async def get_schedule(
    request: Request, week: str, week_start: date | None = None
) -> dict[str, Any]:
    session_id, user = _require_user(request)
    week = _validate_week(week)
    week_start = _validate_week_start(week, week_start)
    try:
        schedule = await _load_schedule(
            session_id, week, selected_start=week_start, user=user
        )
    except SkyengError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "connected": schedule.get("connected", False),
        "events": schedule.get("events", []),
        "fetched_at": schedule.get("fetched_at"),
        "stale": schedule.get("stale", False),
        "error": schedule.get("error"),
    }


@app.post("/api/schedule/sync")
async def sync_schedule(
    request: Request, data: ScheduleSyncRequest
) -> dict[str, Any]:
    session_id, user = _require_user(request)
    week = _validate_week(data.week_label)
    week_start = _validate_week_start(week, data.week_start)
    try:
        schedule = await _load_schedule(
            session_id, week, force=True, selected_start=week_start, user=user
        )
    except SkyengError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "connected": schedule.get("connected", _skyeng_client(session_id, user).is_connected),
        "events": schedule.get("events", []),
        "fetched_at": schedule.get("fetched_at"),
        "stale": schedule.get("stale", False),
        "error": schedule.get("error"),
    }


@app.post("/api/plan/carry-over")
async def carry_over_plan(request: Request, data: CarryOverRequest) -> dict[str, Any]:
    _, user = _require_user(request)
    current_week = _validate_week(data.week_label)
    selected_start = _validate_week_start(current_week, data.week_start)
    previous_start = (selected_start or _week_dates(current_week)[0]) - timedelta(days=7)
    previous_week = week_label(previous_start)
    try:
        rows = sheets.read_week_rows(user["sheet_name"], previous_week)
    except SheetsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    completed = {"✅", "✔", "☑", "готово", "выполнено", "завершено", "completed", "done"}
    unfinished = [
        row for row in rows
        if row.get("task") and row.get("status", "").strip().casefold() not in completed
    ]
    source_lines = [f"Перенести незавершённые задачи с недели {previous_week}:"]
    source_lines.extend(
        f"- {row.get('sphere', '')}: {row['task']}" for row in unfinished
    )
    return {
        "from_week": previous_week,
        "tasks": unfinished,
        "source_text": "\n".join(source_lines) if unfinished else "",
    }


@app.post("/api/plan/generate")
async def generate_plan(request: Request, data: GenerateRequest) -> dict[str, Any]:
    session_id, user = _require_user(request)
    week = _validate_week(data.week_label)
    week_start = _validate_week_start(week, data.week_start)
    planning_text = _compose_planning_text(data.source_text, data.guiding_answers)
    if not _has_planning_details(planning_text):
        raise HTTPException(
            status_code=400,
            detail="Добавь хотя бы одну конкретную задачу: что сделать, когда и примерно сколько времени это займёт.",
        )
    source_text, schedule = await _planning_source(
        session_id,
        week,
        planning_text,
        data.include_schedule,
        week_start,
        user,
    )
    database.update_account_profile(
        user["account_id"],
        {
            "guiding_answers": data.guiding_answers,
            "last_week": week,
            "include_schedule": data.include_schedule,
        },
    )
    try:
        draft = await llm.generate_plan(
            source_text=source_text,
            week_label=week,
            session_id=f"web-{session_id}",
        )
    except (LLMError, PlanValidationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "draft": draft.to_dict(),
        "schedule_events": len(schedule.get("events", [])),
    }


@app.post("/api/plan/update")
async def update_plan(request: Request, data: UpdateRequest) -> dict[str, Any]:
    session_id, user = _require_user(request)
    week = _validate_week(data.week_label)
    week_start = _validate_week_start(week, data.week_start)
    current = _draft_from_payload(data.draft, week)
    _, schedule = await _planning_source(
        session_id,
        week,
        data.correction.strip(),
        data.include_schedule,
        week_start,
        user,
    )
    correction = data.correction.strip()
    if schedule.get("events"):
        correction = f"{render_schedule(schedule['events'])}\n\nПРАВКА ПОЛЬЗОВАТЕЛЯ:\n{correction}"
    try:
        draft = await llm.generate_plan(
            source_text=correction,
            week_label=week,
            current_draft=current,
            correction=correction,
            session_id=f"web-{session_id}",
        )
    except (LLMError, PlanValidationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"draft": draft.to_dict()}


@app.post("/api/plan/confirm")
async def confirm_plan(request: Request, data: ConfirmRequest) -> dict[str, Any]:
    _, user = _require_user(request)
    week = _validate_week(data.week_label)
    draft = _draft_from_payload(data.draft, week)
    try:
        start_row = sheets.append_plan(user["sheet_name"], draft)
    except SheetsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "sheet_name": user["sheet_name"],
        "start_row": start_row,
        "spreadsheet_url": sheets.spreadsheet_url(),
    }


@app.post("/api/reflection/generate")
async def generate_reflection(request: Request, data: ReflectionRequest) -> dict[str, str]:
    session_id, user = _require_user(request)
    week = _validate_week(data.week_label)
    draft = _draft_from_payload(data.draft, week)
    try:
        rows = sheets.read_week_rows(user["sheet_name"], week)
        reflection = await llm.generate_reflection(
            week,
            draft,
            rows=rows,
            notes=data.notes,
            session_id=f"web-reflection-{session_id}",
        )
        sheets.update_reflection(user["sheet_name"], week, reflection)
    except (SheetsError, LLMError, PlanValidationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    database.update_account_profile(
        user["account_id"], {"last_reflection_notes": data.notes.strip()}
    )
    return {"reflection": reflection}
