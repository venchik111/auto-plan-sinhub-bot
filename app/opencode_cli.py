from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from .config import Settings


log = logging.getLogger(__name__)
T = TypeVar("T")


class LLMError(RuntimeError):
    pass


def extract_json(content: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S | re.I)
    if fenced:
        return fenced.group(1)
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        return content[start : end + 1]
    return content


def parse_opencode_events(stdout: str) -> tuple[str, str | None]:
    """Collect model text and an error from `opencode run --format json` output."""
    text_parts: list[str] = []
    error_message: str | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "text":
            part = event.get("part") or {}
            chunk = part.get("text") or event.get("text") or ""
            if isinstance(chunk, str):
                text_parts.append(chunk)
        elif event.get("type") == "error":
            error = event.get("error") or {}
            data = error.get("data") if isinstance(error, dict) else None
            error_message = (
                data.get("message") if isinstance(data, dict) else str(error)
            ) or json.dumps(event, ensure_ascii=False)
    return "".join(text_parts).strip(), error_message


class OpenCodeRunner:
    """Runs `opencode run` for one agent and falls back through models."""

    def __init__(
        self,
        binary: str,
        agent: str,
        models: tuple[str, ...],
        timeout_seconds: int,
        directory: Path,
    ):
        self.binary = binary
        self.agent = agent
        self.models = models
        self.timeout_seconds = timeout_seconds
        self.directory = directory

    @classmethod
    def from_settings(cls, settings: "Settings", agent: str | None = None) -> "OpenCodeRunner":
        return cls(
            binary=settings.opencode_bin,
            agent=agent or settings.opencode_agent,
            models=settings.opencode_models,
            timeout_seconds=settings.opencode_timeout_seconds,
            directory=settings.opencode_directory,
        )

    async def run_with_fallback(
        self,
        prompt: str,
        parse: Callable[[str], T],
        failure_message: str = "OpenCode не справился ни одной моделью.",
    ) -> tuple[T, str]:
        models = self.models or (None,)
        last_error: Exception | None = None
        loop = asyncio.get_running_loop()
        for attempt, model in enumerate(models, start=1):
            model_name = model or "модель по умолчанию"
            log.info(
                "OpenCode CLI (%s): запрос к модели «%s» (попытка %d из %d)",
                self.agent,
                model_name,
                attempt,
                len(models),
            )
            started = loop.time()
            try:
                result = parse(await self.run(prompt, model))
            except (LLMError, ValueError) as exc:
                last_error = exc
                log.warning(
                    "OpenCode CLI (%s): модель «%s» не смогла за %.1f с: %s",
                    self.agent,
                    model_name,
                    loop.time() - started,
                    exc,
                )
                continue
            log.info(
                "OpenCode CLI (%s): модель «%s» ответила за %.1f с",
                self.agent,
                model_name,
                loop.time() - started,
            )
            return result, model_name

        detail = f" Последняя ошибка: {last_error}" if last_error else ""
        raise LLMError(f"{failure_message}{detail}")

    async def run(self, prompt: str, model: str | None) -> str:
        args = [
            "run",
            "--format",
            "json",
            "--agent",
            self.agent,
            *(["--model", model] if model else []),
        ]
        directory = self.directory.resolve()
        child_env = dict(os.environ)
        child_env["PWD"] = str(directory)
        # On Windows the npm shim is `opencode.cmd`; `which` resolves it via PATHEXT.
        binary = shutil.which(self.binary) or self.binary

        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                *args,
                cwd=str(directory),
                env=child_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LLMError(
                f"OpenCode CLI не найден: {self.binary}. Установи opencode-ai или задай OPENCODE_BIN."
            ) from exc
        except OSError as exc:
            raise LLMError(f"Не удалось запустить OpenCode CLI: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise LLMError(f"OpenCode не ответил за {self.timeout_seconds} с") from exc

        content, error_message = parse_opencode_events(stdout.decode("utf-8", "replace"))
        if process.returncode != 0 or error_message:
            stderr_tail = stderr.decode("utf-8", "replace").strip()[-400:]
            raise LLMError(
                f"код выхода {process.returncode}, ошибка={error_message or 'нет'}"
                f"{', stderr=' + stderr_tail if stderr_tail else ''}"
            )
        if not content:
            raise LLMError("OpenCode CLI не вернул текстовый ответ модели.")
        return content
