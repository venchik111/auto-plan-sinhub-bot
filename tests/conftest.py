from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="",
        llm_provider="opencode",
        llm_api_key="",
        llm_model="mimo-v2.5-free",
        llm_base_url="https://opencode.ai/zen/v1",
        opencode_bin="opencode",
        opencode_agent="planner",
        opencode_models=("opencode/model-a", "opencode/model-b"),
        opencode_timeout_seconds=5,
        opencode_directory=tmp_path,
        google_spreadsheet_id="spreadsheet-id",
        google_service_account_file=tmp_path / "service-account.json",
        skyeng_storage_state_file=tmp_path / "skyeng.json",
        database_path=tmp_path / "test.sqlite3",
        app_timezone="Europe/Moscow",
    )
