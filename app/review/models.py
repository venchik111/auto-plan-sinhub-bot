from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ACADEMIC_SPHERES = frozenset({"база", "профиль"})
NON_ACADEMIC_SPHERES = frozenset({"коллектив", "спорт", "личное"})


@dataclass(frozen=True)
class ReviewTask:
    index: int
    sphere: str
    text: str
    day: str
    status: str


@dataclass(frozen=True)
class StudentWeek:
    student: str
    week_label: str
    goals: tuple[str, ...]
    goals_raw: str
    tasks: tuple[ReviewTask, ...]
    reflection: str

    @classmethod
    def empty(cls, student: str, week_label: str) -> "StudentWeek":
        return cls(student, week_label, (), "", (), "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "student": self.student,
            "week_label": self.week_label,
            "goals": list(self.goals),
            "goals_raw": self.goals_raw,
            "tasks": [asdict(task) for task in self.tasks],
            "reflection": self.reflection,
        }


@dataclass(frozen=True)
class Finding:
    source: str
    rule: str
    severity: str
    target: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Finding":
        return cls(
            source=str(raw["source"]),
            rule=str(raw["rule"]),
            severity=str(raw["severity"]),
            target=str(raw["target"]),
            message=str(raw["message"]),
        )


@dataclass(frozen=True)
class Rewrite:
    target: str
    original: str
    suggestion: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Rewrite":
        return cls(str(raw["target"]), str(raw["original"]), str(raw["suggestion"]))


@dataclass(frozen=True)
class LlmReview:
    findings: tuple[Finding, ...]
    rewrites: tuple[Rewrite, ...]
    questions: tuple[str, ...]
    summary: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "rewrites": [rewrite.to_dict() for rewrite in self.rewrites],
            "questions": list(self.questions),
            "summary": self.summary,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LlmReview":
        return cls(
            findings=tuple(Finding.from_dict(item) for item in raw.get("findings", [])),
            rewrites=tuple(Rewrite.from_dict(item) for item in raw.get("rewrites", [])),
            questions=tuple(str(item) for item in raw.get("questions", [])),
            summary=str(raw.get("summary", "")),
            model=str(raw.get("model", "")),
        )
