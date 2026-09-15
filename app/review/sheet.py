from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Lock
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..config import Settings
from ..sheets import SheetsError, SheetsRepository
from ..weeks import canonical_week_label
from .models import ReviewTask, StudentWeek


READONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SERVICE_SHEET_TITLES = frozenset(
    {"инструкция (куратор)", "инструкция (студент)", "дашборд куратора", "список студентов"}
)
EXAMPLE_SHEET_TITLE = "пример"
SHEET_RANGE = "A1:G500"

_GOAL_PREFIX = re.compile(r"^\s*(?:\d+\s*[.)]|[-•—*])\s*")
_INLINE_NUMBERING = re.compile(r"(?:(?<=\s)|^)\d+\s*[.)]\s+")


def normalize_text(value: Any) -> str:
    return SheetsRepository._normalized(str(value or ""))


def split_goals(raw: str) -> tuple[str, ...]:
    lines = [line for line in str(raw or "").replace("\r", "").split("\n") if line.strip()]
    if len(lines) == 1 and len(_INLINE_NUMBERING.findall(lines[0])) > 1:
        lines = [part for part in _INLINE_NUMBERING.split(lines[0]) if part.strip()]
    goals = [" ".join(_GOAL_PREFIX.sub("", line).split()) for line in lines]
    return tuple(goal for goal in goals if goal)


def _cell(row: list[Any], index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def parse_student_rows(student: str, values: list[list[Any]]) -> dict[str, StudentWeek]:
    """Group sheet rows (header included) into weeks.

    Goals and reflection live in the first row of a week; every row of the week
    that has any task column filled is a task. Rows without a valid week label
    are skipped because they cannot be attributed to a week.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in values[1:]:
        if not any(_cell(row, index) for index in range(7)):
            continue
        label = canonical_week_label(_cell(row, 0))
        if label is None:
            continue
        bucket = buckets.setdefault(label, {"goals_raw": "", "reflection": "", "tasks": []})
        if not bucket["goals_raw"] and _cell(row, 1):
            bucket["goals_raw"] = _cell(row, 1)
        if not bucket["reflection"] and _cell(row, 6):
            bucket["reflection"] = _cell(row, 6)
        sphere, text, day, status = (_cell(row, index) for index in (2, 3, 4, 5))
        if sphere or text or day or status:
            bucket["tasks"].append(
                ReviewTask(
                    index=len(bucket["tasks"]) + 1,
                    sphere=sphere,
                    text=" ".join(text.split()),
                    day=day,
                    status=status,
                )
            )
    return {
        label: StudentWeek(
            student=student,
            week_label=label,
            goals=split_goals(bucket["goals_raw"]),
            goals_raw=bucket["goals_raw"],
            tasks=tuple(bucket["tasks"]),
            reflection=bucket["reflection"],
        )
        for label, bucket in buckets.items()
    }


@dataclass(frozen=True)
class Group:
    students: dict[str, dict[str, StudentWeek]]
    example: dict[str, StudentWeek]

    def week_of(self, student: str, week_label: str) -> StudentWeek:
        weeks = self.students[student]
        return weeks.get(week_label) or StudentWeek.empty(student, week_label)


class FreshmenSheetReader:
    def __init__(self, service: Any, spreadsheet_id: str):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self._read_lock = Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "FreshmenSheetReader":
        if not settings.google_service_account_file.exists():
            raise RuntimeError(
                f"Не найден файл сервисного аккаунта: {settings.google_service_account_file}"
            )
        credentials = service_account.Credentials.from_service_account_file(
            str(settings.google_service_account_file), scopes=READONLY_SCOPES
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return cls(service, settings.freshmen_spreadsheet_id)

    def read_group(self) -> Group:
        # googleapiclient's default httplib2 transport is not thread-safe.
        # Queue workers and the HTTP polling path can request the group at the
        # same time, so serialize access to the shared service instance.
        with self._read_lock:
            return self._read_group()

    def _read_group(self) -> Group:
        try:
            meta = self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(title)",
            ).execute()
            titles = [
                item["properties"]["title"]
                for item in meta.get("sheets", [])
                if item.get("properties", {}).get("title")
            ]
            wanted = [title for title in titles if normalize_text(title) not in SERVICE_SHEET_TITLES]
            if not wanted:
                return Group(students={}, example={})
            ranges = [f"'{title.replace(chr(39), chr(39) * 2)}'!{SHEET_RANGE}" for title in wanted]
            response = self.service.spreadsheets().values().batchGet(
                spreadsheetId=self.spreadsheet_id,
                ranges=ranges,
            ).execute()
        except HttpError as exc:
            raise SheetsError(f"Google Sheets API: HTTP {exc.resp.status}") from exc

        students: dict[str, dict[str, StudentWeek]] = {}
        example: dict[str, StudentWeek] = {}
        for title, value_range in zip(wanted, response.get("valueRanges", [])):
            weeks = parse_student_rows(title, value_range.get("values", []))
            if normalize_text(title) == EXAMPLE_SHEET_TITLE:
                example = weeks
            else:
                students[title] = weeks
        return Group(students=students, example=example)
