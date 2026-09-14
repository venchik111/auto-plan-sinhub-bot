from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import Settings
from .models import PlanDraft


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
NON_STUDENT_SHEET_TITLES = {
    "список студентов",
    "дашборд куратора",
    "инструкция",
}


@dataclass(frozen=True)
class Student:
    display_name: str
    sheet_name: str


class SheetsError(RuntimeError):
    pass


class SheetsRepository:
    def __init__(self, service: Any, spreadsheet_id: str):
        self.service = service
        self.spreadsheet_id = spreadsheet_id

    @classmethod
    def from_settings(cls, settings: Settings) -> "SheetsRepository":
        if not settings.google_service_account_file.exists():
            raise RuntimeError(
                f"Не найден файл сервисного аккаунта: {settings.google_service_account_file}"
            )
        credentials = service_account.Credentials.from_service_account_file(
            str(settings.google_service_account_file), scopes=SCOPES
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return cls(service, settings.google_spreadsheet_id)

    def _spreadsheet(self) -> dict[str, Any]:
        try:
            return self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(title)",
            ).execute()
        except HttpError as exc:
            raise SheetsError(f"Google Sheets API: HTTP {exc.resp.status}") from exc

    def _sheet_titles(self) -> list[str]:
        return [
            item["properties"]["title"]
            for item in self._spreadsheet().get("sheets", [])
            if item.get("properties", {}).get("title")
        ]

    @staticmethod
    def _normalized(value: str) -> str:
        cleaned = (
            str(value)
            .replace("\u00a0", " ")
            .replace("\u200b", "")
            .replace("\ufeff", "")
        )
        return " ".join(cleaned.casefold().replace("ё", "е").split())

    @classmethod
    def _resolve_tab(cls, display_name: str, titles: list[str]) -> str | None:
        normalized_name = cls._normalized(display_name)
        if not normalized_name:
            return None

        exact = [title for title in titles if cls._normalized(title) == normalized_name]
        if len(exact) == 1:
            return exact[0]

        # Разрешаем только достаточно длинное и однозначное совпадение по части
        # названия: это помогает, если пользователь вставил Фамилию Имя из
        # вкладки с отчеством, но не даёт короткому тексту случайно выбрать лист.
        if len(normalized_name) < 5 or len(normalized_name.split()) < 2:
            return None
        containing = [
            title
            for title in titles
            if cls._normalized(title) in normalized_name
            or normalized_name in cls._normalized(title)
        ]
        return containing[0] if len(containing) == 1 else None

    def list_students(self) -> list[Student]:
        # Источник истины для регистрации — названия персональных вкладок.
        # Отдельная вкладка «Список студентов» может содержать другой формат ФИО
        # и больше не должна мешать поиску.
        titles = [
            title
            for title in self._sheet_titles()
            if self._normalized(title) not in NON_STUDENT_SHEET_TITLES
        ]
        return [Student(display_name=title, sheet_name=title) for title in titles]

    def find_student(self, display_name: str) -> Student | None:
        titles = [student.sheet_name for student in self.list_students()]
        sheet_name = self._resolve_tab(display_name, titles)
        if not sheet_name:
            return None
        return Student(display_name=sheet_name, sheet_name=sheet_name)

    def append_plan(self, sheet_name: str, draft: PlanDraft) -> int:
        escaped_name = sheet_name.replace("'", "''")
        try:
            existing = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{escaped_name}'!A:G",
            ).execute().get("values", [])
        except HttpError as exc:
            raise SheetsError(f"Google Sheets API: HTTP {exc.resp.status}") from exc
        start_row = max(2, len(existing) + 1)
        rows = draft.as_sheet_rows()
        end_row = start_row + len(rows) - 1
        try:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{escaped_name}'!A{start_row}:G{end_row}",
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            ).execute()
        except HttpError as exc:
            raise SheetsError(f"Google Sheets API: HTTP {exc.resp.status}") from exc
        return start_row

    def spreadsheet_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/edit"
