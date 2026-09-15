from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.curator_web import CURATOR_COOKIE, create_curator_router, curator_token
from app.sheets import SheetsError


class FakeReport:
    def __init__(self, student, status="remarks"):
        self.student = student
        self.week_label = "14.09-20.09"
        self.status = status

    def summary_dict(self):
        return {"student": self.student, "status": self.status, "llm_state": "none", "warnings": 1, "advice": 0}

    def to_dict(self):
        return {**self.summary_dict(), "findings": [], "plan": {"goals": []}}


class FakeService:
    def __init__(self):
        self.fail = False

    def _check(self):
        if self.fail:
            raise SheetsError("Google Sheets API: HTTP 403")

    def students(self):
        self._check()
        return ["Альфа", "Бета"]

    def group_overview(self, week_label):
        self._check()
        return [FakeReport("Альфа"), FakeReport("Бета", "ok")]

    def student_report(self, student, week_label):
        self._check()
        if student not in ("Альфа", "Бета"):
            raise KeyError(student)
        return FakeReport(student)

    def pending_students(self, week_label):
        return ["Альфа", "Бета"]


class FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue_group(self, week_label, students):
        self.calls.append(("group", week_label, tuple(students)))
        return len(students)

    def enqueue_student(self, student, week_label):
        self.calls.append(("student", week_label, student))

    def state_of(self, student, week_label):
        return "running" if student == "Альфа" else None

    def progress(self):
        return {"busy": bool(self.calls), "total": len(self.calls)}


@pytest.fixture
def parts(tmp_path: Path):
    (tmp_path / "curator.html").write_text("<h1>curator</h1>", encoding="utf-8")
    service, queue = FakeService(), FakeQueue()
    app = FastAPI()
    app.include_router(create_curator_router(service, queue, "secret", tmp_path, failed_login_delay=0))
    return TestClient(app), service, queue


def login(client):
    response = client.post("/api/curator/login", json={"password": "secret"})
    assert response.status_code == 200
    return response


def test_page_is_public(parts) -> None:
    client, _, _ = parts
    assert client.get("/curator").text == "<h1>curator</h1>"


def test_api_requires_login(parts) -> None:
    client, _, _ = parts
    assert client.get("/api/curator/review", params={"week": "14.09-20.09"}).status_code == 401
    assert client.get("/api/curator/queue").status_code == 401
    assert client.post("/api/curator/analyze", json={"week_label": "14.09-20.09"}).status_code == 401


def test_wrong_password(parts) -> None:
    client, _, _ = parts
    response = client.post("/api/curator/login", json={"password": "nope"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Неверный пароль."


def test_login_cookie_is_hmac_of_password(parts) -> None:
    client, _, _ = parts
    login(client)
    assert client.cookies.get(CURATOR_COOKIE) == curator_token("secret")
    assert curator_token("secret") != curator_token("other")


def test_review_overview_uses_queue_state(parts) -> None:
    client, _, _ = parts
    login(client)
    response = client.get("/api/curator/review", params={"week": "07.09-13.09"})
    data = response.json()
    assert data["week_label"] == "7.09-13.09"
    assert [(s["student"], s["llm_state"]) for s in data["students"]] == [("Альфа", "running"), ("Бета", "none")]
    assert "queue" in data


def test_bad_week_and_unknown_student(parts) -> None:
    client, _, _ = parts
    login(client)
    assert client.get("/api/curator/review", params={"week": "неделя"}).status_code == 400
    response = client.get("/api/curator/review/student", params={"name": "Никто", "week": "14.09-20.09"})
    assert response.status_code == 404


def test_student_report(parts) -> None:
    client, _, _ = parts
    login(client)
    response = client.get("/api/curator/review/student", params={"name": "Бета", "week": "14.09-20.09"})
    assert response.status_code == 200
    assert response.json()["plan"] == {"goals": []}


def test_student_pdf_export(parts, monkeypatch) -> None:
    monkeypatch.setattr("app.curator_web.build_student_recommendations_pdf", lambda report: b"%PDF-test")
    client, _, _ = parts
    login(client)

    response = client.get(
        "/api/curator/review/student.pdf",
        params={"name": "Бета", "week": "14.09-20.09"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''%D1%80%D0%B5%D0%BA%D0%BE%D0%BC%D0%B5%D0%BD%D0%B4%D0%B0%D1%86%D0%B8%D0%B8-%D0%91%D0%B5%D1%82%D0%B0-14.09-20.09.pdf" in response.headers["content-disposition"]
    assert response.content == b"%PDF-test"


def test_analyze_group_and_student(parts) -> None:
    client, _, queue = parts
    login(client)
    group = client.post("/api/curator/analyze", json={"week_label": "14.09-20.09"}).json()
    single = client.post("/api/curator/analyze", json={"week_label": "14.09-20.09", "student": "Бета"}).json()
    unknown = client.post("/api/curator/analyze", json={"week_label": "14.09-20.09", "student": "Никто"})

    assert group["added"] == 2
    assert single["added"] == 1
    assert unknown.status_code == 404
    assert queue.calls == [("group", "14.09-20.09", ("Альфа", "Бета")), ("student", "14.09-20.09", "Бета")]


def test_sheets_error_is_502(parts) -> None:
    client, service, _ = parts
    login(client)
    service.fail = True
    response = client.get("/api/curator/review", params={"week": "14.09-20.09"})
    assert response.status_code == 502
    assert "HTTP 403" in response.json()["detail"]


def test_logout(parts) -> None:
    client, _, _ = parts
    login(client)
    client.post("/api/curator/logout")
    assert client.get("/api/curator/queue").status_code == 401


def test_settings_defaults(settings) -> None:
    assert settings.freshmen_spreadsheet_id == ""
    assert settings.curator_password == ""
    assert settings.opencode_review_agent == "reviewer"
