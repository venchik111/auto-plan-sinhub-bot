from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .database import Database
from .llm import LLMError, OpenAICompatibleLLM
from .models import DAYS, SPHERES, PlanDraft, PlanValidationError
from .skyeng import SkyengAuthError, SkyengError, SkyengScheduleClient, render_schedule
from .sheets import SheetsError, SheetsRepository
from .weeks import is_week_label, local_today, week_label


BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
SESSION_COOKIE = "auto_plan_session"
SESSION_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SCHEDULE_CACHE_VERSION = 2

settings = Settings.from_env()
database = Database(settings.database_path)
database.init()
sheets = SheetsRepository.from_settings(settings)
llm = OpenAICompatibleLLM(settings)

# A browser session is the current account boundary for the local MVP. Each
# session gets its own Skyeng storage state, so one user's account never leaks
# into another user's schedule.
skyeng_auth_processes: dict[str, asyncio.subprocess.Process] = {}
skyeng_auth_errors: dict[str, str] = {}

app = FastAPI(title="Авто-планирование", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


class RegisterRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=150)


class GenerateRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    source_text: str = Field(min_length=10, max_length=8000)
    include_schedule: bool = True


class UpdateRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    draft: dict[str, Any]
    correction: str = Field(min_length=3, max_length=4000)
    include_schedule: bool = True


class ConfirmRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    draft: dict[str, Any]


class ScheduleSyncRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)


def _skyeng_storage_path(session_id: str) -> Path:
    directory = settings.skyeng_storage_state_file.parent / "skyeng"
    return directory / f"{session_id}.json"


def _skyeng_client(session_id: str) -> SkyengScheduleClient:
    return SkyengScheduleClient(_skyeng_storage_path(session_id))


async def _watch_skyeng_auth(
    session_id: str, process: asyncio.subprocess.Process
) -> None:
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        output = (stderr or stdout).decode("utf-8", "replace").strip()
        if "TargetClosedError" in output or "target, context or browser has been closed" in output:
            message = "Окно входа Skyeng закрыли до завершения авторизации."
        elif "Executable doesn't exist" in output:
            message = "Не найден браузер для окна входа Skyeng."
        else:
            message = "Не удалось завершить подключение Skyeng. Попробуй ещё раз."
        skyeng_auth_errors[session_id] = message
    if skyeng_auth_processes.get(session_id) is process:
        skyeng_auth_processes.pop(session_id, None)


def _new_session_id() -> str:
    return uuid.uuid4().hex


def _session(request: Request) -> tuple[str, dict[str, Any] | None, bool]:
    session_id = request.cookies.get(SESSION_COOKIE, "")
    if not SESSION_PATTERN.fullmatch(session_id):
        session_id = _new_session_id()
        return session_id, None, True
    return session_id, database.get_web_session(session_id), False


def _json_with_cookie(
    payload: dict[str, Any], session_id: str, is_new: bool
) -> JSONResponse:
    response = JSONResponse(payload)
    if is_new:
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=60 * 60 * 24 * 90,
            httponly=True,
            samesite="lax",
        )
    return response


def _require_user(request: Request) -> tuple[str, dict[str, Any]]:
    session_id, user, _ = _session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Сначала укажи ФИО вкладки.")
    return session_id, user


def _validate_week(value: str) -> str:
    value = value.strip()
    if not is_week_label(value):
        raise HTTPException(status_code=400, detail="Укажи неделю в формате 14.09-20.09.")
    return value


def _has_planning_details(value: str) -> bool:
    details = re.sub(
        r"^(Главный результат|Фиксированные дела|Ресурс и время|Не забыть):?\s*$",
        "",
        value.strip(),
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return len(" ".join(details.split())) >= 10


def _week_dates(value: str) -> tuple[date, date]:
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
    session_id: str, week: str, force: bool = False
) -> dict[str, Any]:
    skyeng = _skyeng_client(session_id)
    cached = database.get_web_schedule(session_id, week)
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

    start, end = _week_dates(week)
    try:
        events = await skyeng.fetch_week(start, end)
    except SkyengAuthError:
        if cached:
            return {
                "events": cached.get("events", []),
                "fetched_at": cached.get("fetched_at"),
                "connected": True,
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
    database.save_web_schedule(session_id, week, event_payload, fetched_at)
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
) -> tuple[str, dict[str, Any]]:
    skyeng = _skyeng_client(session_id)
    if not include_schedule:
        return user_text, {
            "events": [],
            "connected": skyeng.is_connected,
            "stale": False,
            "included": False,
        }
    try:
        schedule = await _load_schedule(session_id, week)
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
async def connect_skyeng(request: Request) -> dict[str, str | bool]:
    session_id, _ = _require_user(request)
    skyeng = _skyeng_client(session_id)
    if skyeng.is_connected:
        return {"connected": True, "status": "connected"}

    existing = skyeng_auth_processes.get(session_id)
    if existing and existing.returncode is None:
        return {"connected": False, "status": "pending"}

    skyeng_auth_errors.pop(session_id, None)
    target = _skyeng_storage_path(session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.skyeng_login",
            "--storage-state",
            str(target),
            "--auto",
            cwd=str(BASE_DIR),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось открыть окно входа Skyeng: {exc}"
        ) from exc
    skyeng_auth_processes[session_id] = process
    asyncio.create_task(_watch_skyeng_auth(session_id, process))
    return {"connected": False, "status": "pending"}


@app.get("/api/skyeng/status")
async def skyeng_status(request: Request) -> dict[str, str | bool]:
    session_id, _ = _require_user(request)
    skyeng = _skyeng_client(session_id)
    if skyeng.is_connected:
        return {"connected": True, "status": "connected"}
    if session_id in skyeng_auth_processes:
        return {"connected": False, "status": "pending"}
    return {
        "connected": False,
        "status": "failed" if session_id in skyeng_auth_errors else "idle",
        "error": skyeng_auth_errors.get(session_id, ""),
    }


@app.get("/api/bootstrap")
async def bootstrap(request: Request) -> JSONResponse:
    session_id, user, is_new = _session(request)
    today = local_today(settings.app_timezone)
    return _json_with_cookie(
        {
            "user": user,
            "current_week": week_label(today),
            "next_week": week_label(today, 1),
            "spheres": list(SPHERES),
            "days": list(DAYS),
        },
        session_id,
        is_new,
    )


@app.post("/api/register")
async def register(request: Request, data: RegisterRequest) -> JSONResponse:
    session_id, _, is_new = _session(request)
    try:
        student = sheets.find_student(data.display_name.strip())
    except SheetsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not student:
        raise HTTPException(
            status_code=404,
            detail="Не нашёл вкладку с таким названием. Скопируй ФИО из заголовка листа.",
        )
    database.register_web_session(session_id, student.display_name, student.sheet_name)
    return _json_with_cookie(
        {
            "user": {
                "display_name": student.display_name,
                "sheet_name": student.sheet_name,
            }
        },
        session_id,
        is_new,
    )


@app.get("/api/schedule")
async def get_schedule(request: Request, week: str) -> dict[str, Any]:
    session_id, _ = _require_user(request)
    week = _validate_week(week)
    try:
        schedule = await _load_schedule(session_id, week)
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
    session_id, _ = _require_user(request)
    week = _validate_week(data.week_label)
    try:
        schedule = await _load_schedule(session_id, week, force=True)
    except SkyengError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "connected": schedule.get("connected", _skyeng_client(session_id).is_connected),
        "events": schedule.get("events", []),
        "fetched_at": schedule.get("fetched_at"),
        "stale": schedule.get("stale", False),
        "error": schedule.get("error"),
    }


@app.post("/api/plan/generate")
async def generate_plan(request: Request, data: GenerateRequest) -> dict[str, Any]:
    session_id, _ = _require_user(request)
    week = _validate_week(data.week_label)
    if not _has_planning_details(data.source_text):
        raise HTTPException(
            status_code=400,
            detail="Добавь хотя бы одну конкретную задачу: что сделать, когда и примерно сколько времени это займёт.",
        )
    source_text, schedule = await _planning_source(
        session_id,
        week,
        data.source_text.strip(),
        data.include_schedule,
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
    session_id, _ = _require_user(request)
    week = _validate_week(data.week_label)
    current = _draft_from_payload(data.draft, week)
    _, schedule = await _planning_source(
        session_id,
        week,
        data.correction.strip(),
        data.include_schedule,
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
