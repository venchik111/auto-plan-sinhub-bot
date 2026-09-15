from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    opencode_bin: str
    opencode_agent: str
    opencode_models: tuple[str, ...]
    opencode_timeout_seconds: int
    opencode_directory: Path
    google_spreadsheet_id: str
    google_service_account_file: Path
    skyeng_storage_state_file: Path
    database_path: Path
    app_timezone: str
    freshmen_spreadsheet_id: str = ""
    curator_password: str = ""
    opencode_review_agent: str = "reviewer"
    curator_students: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise RuntimeError(f"Не задана переменная окружения {name}")
            return value

        llm_provider = os.getenv("LLM_PROVIDER", "opencode").strip().lower()
        opencode_bin = os.getenv("OPENCODE_BIN", "opencode").strip()
        opencode_agent = os.getenv("OPENCODE_AGENT", "planner").strip()
        opencode_timeout_seconds = int(os.getenv("OPENCODE_TIMEOUT_SECONDS", "180"))
        opencode_directory = Path(
            os.getenv("OPENCODE_DIR", "./opencode")
        ).expanduser()
        if llm_provider == "opencode":
            # OpenCode is called through its CLI. The CLI owns the session and
            # provider authentication, as in the reference bot implementation.
            llm_api_key = os.getenv("OPENCODE_API_KEY", "").strip()
            llm_model = os.getenv("OPENCODE_MODEL", "mimo-v2.5-free").strip()
            llm_base_url = os.getenv(
                "OPENCODE_BASE_URL", "https://opencode.ai/zen/v1"
            ).rstrip("/")
            configured_models = tuple(
                model.strip()
                for model in os.getenv("OPENCODE_MODELS", "").split(",")
                if model.strip()
            )
            if configured_models:
                opencode_models = configured_models
            else:
                opencode_models = (
                    llm_model if "/" in llm_model else f"opencode/{llm_model}",
                )
        elif llm_provider == "openrouter":
            llm_api_key = required("OPENROUTER_API_KEY")
            llm_model = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
            llm_base_url = os.getenv(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ).rstrip("/")
            opencode_models = ()
        else:
            raise RuntimeError(
                "LLM_PROVIDER должен быть opencode или openrouter"
            )

        if not llm_model:
            raise RuntimeError("Не задана модель LLM")

        return cls(
            # The web app and Skyeng login helper do not need Telegram.
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            opencode_bin=opencode_bin,
            opencode_agent=opencode_agent,
            opencode_models=opencode_models,
            opencode_timeout_seconds=opencode_timeout_seconds,
            opencode_directory=opencode_directory,
            google_spreadsheet_id=required("GOOGLE_SPREADSHEET_ID"),
            google_service_account_file=Path(
                required("GOOGLE_SERVICE_ACCOUNT_FILE")
            ).expanduser(),
            skyeng_storage_state_file=Path(
                os.getenv(
                    "SKYENG_STORAGE_STATE_FILE",
                    "./credentials/skyeng-storage.json",
                )
            ).expanduser(),
            database_path=Path(os.getenv("DATABASE_PATH", "./bot.sqlite3")).expanduser(),
            app_timezone=os.getenv("APP_TIMEZONE", "Europe/Moscow").strip(),
            freshmen_spreadsheet_id=os.getenv("FRESHMEN_SPREADSHEET_ID", "").strip(),
            curator_password=os.getenv("CURATOR_PASSWORD", "").strip(),
            opencode_review_agent=os.getenv("OPENCODE_REVIEW_AGENT", "reviewer").strip() or "reviewer",
            curator_students=tuple(
                " ".join(student.split())
                for student in os.getenv("CURATOR_STUDENTS", "").split(",")
                if student.strip()
            ),
        )
