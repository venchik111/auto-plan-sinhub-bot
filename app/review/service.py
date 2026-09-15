from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from ..database import Database
from ..opencode_cli import LLMError
from ..weeks import week_has_ended
from .models import Finding, LlmReview, StudentWeek
from .rules import evaluate
from .sheet import Group


STATUS_ORDER = {"discuss": 0, "remarks": 1, "ok": 2, "empty": 3}
PENDING_LLM_STATES = frozenset({"none", "stale", "error"})


def data_hash(week: StudentWeek) -> str:
    canonical = json.dumps(week.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_status(findings: Iterable[Finding]) -> str:
    items = list(findings)
    if any(finding.severity == "blocker" for finding in items):
        return "empty"
    warnings = sum(finding.severity == "warning" for finding in items)
    if warnings >= 3:
        return "discuss"
    if warnings:
        return "remarks"
    return "ok"


@dataclass(frozen=True)
class StudentReport:
    student: str
    week_label: str
    status: str
    llm_state: str
    findings: tuple[Finding, ...]
    review: LlmReview | None
    analyzed_at: str | None
    error: str | None
    week: StudentWeek

    def summary_dict(self) -> dict[str, Any]:
        return {
            "student": self.student,
            "status": self.status,
            "llm_state": self.llm_state,
            "warnings": sum(f.severity == "warning" for f in self.findings),
            "advice": sum(f.severity == "advice" for f in self.findings),
        }

    def to_dict(self) -> dict[str, Any]:
        review = self.review
        return {
            **self.summary_dict(),
            "week_label": self.week_label,
            "findings": [finding.to_dict() for finding in self.findings],
            "rewrites": [rewrite.to_dict() for rewrite in review.rewrites] if review else [],
            "questions": list(review.questions) if review else [],
            "summary": review.summary if review else "",
            "model": review.model if review else None,
            "analyzed_at": self.analyzed_at,
            "error": self.error,
            "plan": self.week.to_dict(),
        }


class ReviewService:
    def __init__(
        self,
        reader: Any,
        analyzer: Any,
        database: Database,
        today: Callable[[], date],
        allowed_students: Collection[str] | None = None,
    ):
        self.reader = reader
        self.analyzer = analyzer
        self.database = database
        self.today = today
        self.allowed_students = (
            frozenset(" ".join(student.split()) for student in allowed_students)
            if allowed_students is not None
            else None
        )

    def _is_allowed(self, student: str) -> bool:
        return self.allowed_students is None or " ".join(student.split()) in self.allowed_students

    def students(self) -> list[str]:
        return sorted(
            student
            for student in self.reader.read_group().students
            if self._is_allowed(student)
        )

    def group_overview(self, week_label: str) -> list[StudentReport]:
        group = self.reader.read_group()
        reports = [
            self._report(group, student, week_label)
            for student in group.students
            if self._is_allowed(student)
        ]
        return sorted(reports, key=lambda report: (STATUS_ORDER[report.status], report.student))

    def student_report(self, student: str, week_label: str) -> StudentReport:
        group = self.reader.read_group()
        if student not in group.students or not self._is_allowed(student):
            raise KeyError(student)
        return self._report(group, student, week_label)

    def pending_students(self, week_label: str) -> list[str]:
        return [
            report.student
            for report in self.group_overview(week_label)
            if report.status != "empty" and report.llm_state in PENDING_LLM_STATES
        ]

    async def analyze_student(self, student: str, week_label: str) -> None:
        group = await asyncio.to_thread(self.reader.read_group)
        if student not in group.students or not self._is_allowed(student):
            raise KeyError(student)
        week = group.week_of(student, week_label)
        today = self.today()
        formal = evaluate(week, group.example.get(week_label), today)
        if compute_status(formal) == "empty":
            return
        current_hash = data_hash(week)
        analyzed_at = datetime.now(timezone.utc).isoformat()
        try:
            review = await self.analyzer.analyze(week, formal, week_has_ended(week_label, today))
        except LLMError as exc:
            previous = self.database.get_review_result(student, week_label)
            self.database.save_review_result(
                student,
                week_label,
                previous["data_hash"] if previous else current_hash,
                previous["llm_json"] if previous else None,
                previous["model"] if previous else None,
                str(exc),
                analyzed_at,
            )
            raise
        self.database.save_review_result(
            student,
            week_label,
            current_hash,
            json.dumps(review.to_dict(), ensure_ascii=False),
            review.model,
            None,
            analyzed_at,
        )

    def _report(self, group: Group, student: str, week_label: str) -> StudentReport:
        week = group.week_of(student, week_label)
        formal = evaluate(week, group.example.get(week_label), self.today())
        review: LlmReview | None = None
        llm_state = "none"
        analyzed_at: str | None = None
        error: str | None = None
        if compute_status(formal) != "empty":
            cached = self.database.get_review_result(student, week_label)
            if cached:
                analyzed_at = cached["analyzed_at"]
                error = cached["error"]
                if cached["llm_json"]:
                    review = LlmReview.from_dict(json.loads(cached["llm_json"]))
                if error:
                    llm_state = "error"
                elif review is None:
                    llm_state = "none"
                elif cached["data_hash"] != data_hash(week):
                    llm_state = "stale"
                else:
                    llm_state = "fresh"
        findings = tuple(formal) + (review.findings if review else ())
        return StudentReport(
            student=student,
            week_label=week_label,
            status=compute_status(findings),
            llm_state=llm_state,
            findings=findings,
            review=review,
            analyzed_at=analyzed_at,
            error=error,
            week=week,
        )
