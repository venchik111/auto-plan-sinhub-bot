import asyncio
import json

import pytest

from app.llm import OpenAICompatibleLLM
from app.opencode_cli import LLMError, OpenCodeRunner, extract_json, parse_opencode_events


def test_parse_opencode_events_joins_text_chunks() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "step_start"}),
            json.dumps({"type": "text", "part": {"text": '{"a": '}}),
            "not json",
            json.dumps({"type": "text", "part": {"text": "1}"}}),
        ]
    )
    assert parse_opencode_events(stdout) == ('{"a": 1}', None)


def test_parse_opencode_events_reads_error_message() -> None:
    stdout = json.dumps({"type": "error", "error": {"data": {"message": "rate limit"}}})
    assert parse_opencode_events(stdout) == ("", "rate limit")


def test_extract_json_handles_markdown_and_extra_text() -> None:
    assert extract_json('Вот ответ:\n```json\n{"x": 1}\n```') == '{"x": 1}'
    assert extract_json('Ответ: {"x": {"y": 2}} спасибо') == '{"x": {"y": 2}}'


class ScriptedRunner(OpenCodeRunner):
    def __init__(self, answers: dict[str, object], tmp_path) -> None:
        super().__init__("opencode", "planner", tuple(answers), 5, tmp_path)
        self.answers = answers
        self.calls: list[str | None] = []

    async def run(self, prompt: str, model: str | None) -> str:
        self.calls.append(model)
        answer = self.answers[model]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_run_with_fallback_tries_next_model(tmp_path) -> None:
    runner = ScriptedRunner({"m1": LLMError("timeout"), "m2": "ok"}, tmp_path)
    result, model = asyncio.run(runner.run_with_fallback("prompt", str.upper))
    assert (result, model) == ("OK", "m2")
    assert runner.calls == ["m1", "m2"]


def test_run_with_fallback_treats_parse_error_as_model_failure(tmp_path) -> None:
    def parse(content: str) -> str:
        if content == "bad":
            raise ValueError("not parseable")
        return content

    runner = ScriptedRunner({"m1": "bad", "m2": "good"}, tmp_path)
    assert asyncio.run(runner.run_with_fallback("prompt", parse)) == ("good", "m2")


def test_run_with_fallback_raises_with_failure_message(tmp_path) -> None:
    runner = ScriptedRunner({"m1": LLMError("a"), "m2": LLMError("b")}, tmp_path)
    with pytest.raises(LLMError, match="Не вышло. Последняя ошибка: b"):
        asyncio.run(runner.run_with_fallback("prompt", str, failure_message="Не вышло."))


def test_planner_uses_opencode_runner(settings, tmp_path) -> None:
    llm = OpenAICompatibleLLM(settings)
    plan_json = json.dumps(
        {
            "goals": ["Сдать реферат", "Сходить в зал"],
            "tasks": [{"sphere": "База", "task": "Дописать реферат", "day": "Пн", "time_minutes": 60}],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    llm.opencode = ScriptedRunner({"m1": LLMError("down"), "m2": plan_json}, tmp_path)
    draft = asyncio.run(llm.generate_plan("Дописать реферат в понедельник", "14.09-20.09"))
    assert draft.goals == ("Сдать реферат", "Сходить в зал")
    assert draft.tasks[0].text == "Дописать реферат"
