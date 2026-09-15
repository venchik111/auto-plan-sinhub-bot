from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .sheets import SheetsError
from .review.pdf import build_student_recommendations_pdf
from .weeks import canonical_week_label


CURATOR_COOKIE = "auto_plan_curator"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def curator_token(password: str) -> str:
    return hmac.new(password.encode("utf-8"), b"curator", hashlib.sha256).hexdigest()


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AnalyzeRequest(BaseModel):
    week_label: str = Field(min_length=5, max_length=30)
    student: str | None = Field(default=None, max_length=150)


def create_curator_router(
    service: Any,
    queue: Any,
    password: str,
    web_dir: Path,
    failed_login_delay: float = 1.0,
) -> APIRouter:
    router = APIRouter()
    expected_token = curator_token(password)

    def require_curator(request: Request) -> None:
        token = request.cookies.get(CURATOR_COOKIE, "")
        if not hmac.compare_digest(token, expected_token):
            raise HTTPException(status_code=401, detail="Нужен вход куратора.")

    def parse_week(value: str) -> str:
        label = canonical_week_label(value)
        if label is None:
            raise HTTPException(status_code=400, detail="Укажи неделю в формате 14.09-20.09.")
        return label

    async def call_service(method: Any, *args: Any) -> Any:
        try:
            return await asyncio.to_thread(method, *args)
        except SheetsError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    def with_queue_state(report: Any, data: dict[str, Any]) -> dict[str, Any]:
        state = queue.state_of(report.student, report.week_label)
        if state:
            data["llm_state"] = state
        return data

    curator_only = [Depends(require_curator)]

    @router.get("/curator")
    async def curator_page() -> FileResponse:
        return FileResponse(web_dir / "curator.html")

    @router.post("/api/curator/login")
    async def login(data: LoginRequest) -> JSONResponse:
        if not hmac.compare_digest(curator_token(data.password), expected_token):
            await asyncio.sleep(failed_login_delay)
            raise HTTPException(status_code=401, detail="Неверный пароль.")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            CURATOR_COOKIE,
            expected_token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.post("/api/curator/logout")
    async def logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(CURATOR_COOKIE)
        return response

    @router.get("/api/curator/review", dependencies=curator_only)
    async def review(week: str) -> dict[str, Any]:
        label = parse_week(week)
        reports = await call_service(service.group_overview, label)
        return {
            "week_label": label,
            "students": [with_queue_state(report, report.summary_dict()) for report in reports],
            "queue": queue.progress(),
        }

    @router.get("/api/curator/review/student", dependencies=curator_only)
    async def student_review(name: str, week: str) -> dict[str, Any]:
        label = parse_week(week)
        try:
            report = await call_service(service.student_report, name, label)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Студент не найден.") from exc
        return with_queue_state(report, report.to_dict())

    @router.get("/api/curator/review/student.pdf", dependencies=curator_only)
    async def student_pdf(name: str, week: str) -> Response:
        label = parse_week(week)
        try:
            report = await call_service(service.student_report, name, label)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Студент не найден.") from exc
        if report.status == "empty":
            raise HTTPException(status_code=400, detail="Для пустого плана пока нечего экспортировать.")
        content = await asyncio.to_thread(build_student_recommendations_pdf, report)
        safe_name = "".join(char if char.isalnum() or char in " -_" else "_" for char in report.student).strip()
        filename = f"рекомендации-{safe_name}-{report.week_label}.pdf"
        disposition = f"attachment; filename=\"recommendations.pdf\"; filename*=UTF-8''{quote(filename)}"
        return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": disposition})

    @router.post("/api/curator/analyze", dependencies=curator_only)
    async def analyze(data: AnalyzeRequest) -> dict[str, Any]:
        label = parse_week(data.week_label)
        if data.student:
            if data.student not in await call_service(service.students):
                raise HTTPException(status_code=404, detail="Студент не найден.")
            queue.enqueue_student(data.student, label)
            added = 1
        else:
            added = queue.enqueue_group(label, await call_service(service.pending_students, label))
        return {"added": added, "queue": queue.progress()}

    @router.get("/api/curator/queue", dependencies=curator_only)
    async def queue_progress() -> dict[str, Any]:
        return queue.progress()

    return router
