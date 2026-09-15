import asyncio
import json

import pytest

from app.opencode_cli import LLMError
from app.review.analyzer import ReviewAnalyzer, build_prompt, parse_review
from app.review.models import Finding, ReviewTask, StudentWeek

WEEK = StudentWeek(
    student="Студент",
    week_label="14.09-20.09",
    goals=("Подтянуть матан", "Не сидеть в телефоне"),
    goals_raw="1. Подтянуть матан\n2. Не сидеть в телефоне",
    tasks=(
        ReviewTask(1, "База", "Учиться лучше", "Пн", ""),
        ReviewTask(2, "Спорт", "Пробежка 30 минут", "Вт", "✅"),
    ),
    reflection="Было норм",
)
FORMAL = [Finding("rules", "no_balance", "advice", "week", "Нет задач на личное.")]


def test_prompt_contains_numbered_data_and_known_findings() -> None:
    prompt = build_prompt(WEEK, FORMAL, week_ended=False)
    assert "goal:1. Подтянуть матан" in prompt
    assert "task:1. [База] [Пн] Учиться лучше" in prompt
    assert "task:2. [Спорт] [Вт] Пробежка 30 минут — статус ✅" in prompt
    assert "- week: Нет задач на личное." in prompt
    assert "Рефлексия" not in prompt


def test_prompt_includes_reflection_after_week_end() -> None:
    assert "Рефлексия:\nБыло норм" in build_prompt(WEEK, [], week_ended=True)


def payload(**overrides):
    data = {
        "findings": [
            {"target": "goal:1", "rule": "measurable", "severity": "warning", "message": "Цель неизмерима"},
            {"target": "goal:9", "rule": "measurable", "severity": "warning", "message": "Нет такой цели"},
            {"target": "task:1", "rule": "made_up", "severity": "warning", "message": "Неизвестное правило"},
            {"target": "task:1", "rule": "vague_task", "severity": "blocker", "message": "Недопустимый уровень"},
            {"target": "reflection", "rule": "reflection_quality", "severity": "advice", "message": "Рефлексия до конца недели"},
            {"target": "task:1", "rule": "vague_task", "severity": "advice", "message": "Размытая задача"},
        ],
        "rewrites": [
            {"target": "goal:1", "original": "Подтянуть матан", "suggestion": "Решить 15 задач из модуля 4"},
            {"target": "week", "original": "x", "suggestion": "y"},
        ],
        "questions": ["В1", "В2", "В3", "В4", "В5", ""],
        "summary": "С" * 600,
    }
    data.update(overrides)
    return data


def test_parse_review_filters_invalid_items() -> None:
    content = "Вот анализ:\n```json\n" + json.dumps(payload(), ensure_ascii=False) + "\n```"
    review = parse_review(content, WEEK, week_ended=False)

    assert [(f.rule, f.target, f.severity) for f in review.findings] == [
        ("measurable", "goal:1", "warning"),
        ("vague_task", "task:1", "advice"),
    ]
    assert all(f.source == "llm" for f in review.findings)
    assert [(r.target, r.suggestion) for r in review.rewrites] == [("goal:1", "Решить 15 задач из модуля 4")]
    assert review.questions == ("В1", "В2", "В3", "В4")
    assert len(review.summary) == 500
    assert review.model == ""


def test_parse_review_accepts_reflection_target_after_week_end() -> None:
    review = parse_review(json.dumps(payload(), ensure_ascii=False), WEEK, week_ended=True)
    assert ("reflection_quality", "reflection") in [(f.rule, f.target) for f in review.findings]


@pytest.mark.parametrize("content", ["не json вовсе", "[1, 2, 3]"])
def test_parse_review_rejects_broken_answers(content) -> None:
    with pytest.raises(LLMError):
        parse_review(content, WEEK, week_ended=False)


class FakeRunner:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompt = None

    async def run_with_fallback(self, prompt, parse, failure_message=""):
        self.prompt = prompt
        return parse(self.content), "opencode/model-b"


def test_analyzer_sets_model_name() -> None:
    runner = FakeRunner(json.dumps(payload(), ensure_ascii=False))
    review = asyncio.run(ReviewAnalyzer(runner).analyze(WEEK, FORMAL, week_ended=False))
    assert review.model == "opencode/model-b"
    assert "goal:1. Подтянуть матан" in runner.prompt
