from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    sheet_name TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    telegram_id INTEGER PRIMARY KEY,
                    phase TEXT NOT NULL,
                    week_label TEXT,
                    payload TEXT,
                    source_text TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_sessions (
                    session_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    sheet_name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_schedule_cache (
                    session_id TEXT NOT NULL,
                    week_label TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, week_label)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_results (
                    student TEXT NOT NULL,
                    week_label TEXT NOT NULL,
                    data_hash TEXT NOT NULL,
                    llm_json TEXT,
                    model TEXT,
                    error TEXT,
                    analyzed_at TEXT NOT NULL,
                    PRIMARY KEY (student, week_label)
                )
                """
            )

    def register_user(self, telegram_id: int, display_name: str, sheet_name: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO users (telegram_id, display_name, sheet_name)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    sheet_name=excluded.sheet_name
                """,
                (telegram_id, display_name, sheet_name),
            )

    def get_user(self, telegram_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT telegram_id, display_name, sheet_name FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return dict(row) if row else None

    def register_web_session(
        self, session_id: str, display_name: str, sheet_name: str
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO web_sessions (session_id, display_name, sheet_name)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    sheet_name=excluded.sheet_name
                """,
                (session_id, display_name, sheet_name),
            )

    def get_web_session(self, session_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT session_id, display_name, sheet_name
                FROM web_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_web_schedule(
        self,
        session_id: str,
        week_label: str,
        events: list[dict[str, Any]],
        fetched_at: str,
    ) -> None:
        payload = json.dumps(
            {"parser_version": 2, "events": events}, ensure_ascii=False
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO web_schedule_cache (session_id, week_label, payload, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, week_label) DO UPDATE SET
                    payload=excluded.payload,
                    fetched_at=excluded.fetched_at
                """,
                (session_id, week_label, payload, fetched_at),
            )

    def get_web_schedule(
        self, session_id: str, week_label: str
    ) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT session_id, week_label, payload, fetched_at
                FROM web_schedule_cache
                WHERE session_id = ? AND week_label = ?
                """,
                (session_id, week_label),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            payload = json.loads(result.pop("payload"))
            if isinstance(payload, dict) and isinstance(payload.get("events"), list):
                result["parser_version"] = int(payload.get("parser_version", 1))
                result["events"] = payload["events"]
            elif isinstance(payload, list):
                # Cache entries written before the parser started exporting
                # detailed lesson names are intentionally treated as version 1.
                result["parser_version"] = 1
                result["events"] = payload
            else:
                result["parser_version"] = 1
                result["events"] = []
        except (TypeError, ValueError, json.JSONDecodeError):
            result["parser_version"] = 1
            result["events"] = []
        return result

    def set_session(
        self,
        telegram_id: int,
        phase: str,
        week_label: str | None = None,
        payload: str | None = None,
        source_text: str | None = None,
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO sessions (telegram_id, phase, week_label, payload, source_text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    phase=excluded.phase,
                    week_label=excluded.week_label,
                    payload=excluded.payload,
                    source_text=excluded.source_text
                """,
                (telegram_id, phase, week_label, payload, source_text),
            )

    def get_session(self, telegram_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT telegram_id, phase, week_label, payload, source_text
                FROM sessions WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_draft(self, telegram_id: int, week_label: str, draft: dict[str, Any], source_text: str) -> None:
        self.set_session(
            telegram_id,
            phase="review",
            week_label=week_label,
            payload=json.dumps(draft, ensure_ascii=False),
            source_text=source_text,
        )

    def set_edit_mode(self, telegram_id: int) -> None:
        session = self.get_session(telegram_id)
        if session:
            self.set_session(
                telegram_id,
                phase="edit",
                week_label=session["week_label"],
                payload=session["payload"],
                source_text=session["source_text"],
            )

    def clear_session(self, telegram_id: int) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM sessions WHERE telegram_id = ?", (telegram_id,))

    def claim_draft(self, telegram_id: int) -> dict[str, Any] | None:
        """Atomically reserve a review draft so a double-click cannot write twice."""
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM sessions WHERE telegram_id = ? AND phase = 'review'",
                (telegram_id,),
            ).fetchone()
            if not row:
                return None
            updated = connection.execute(
                "UPDATE sessions SET phase = 'writing' WHERE telegram_id = ? AND phase = 'review'",
                (telegram_id,),
            )
            if updated.rowcount != 1:
                return None
        result = dict(row)
        result["phase"] = "writing"
        return result

    def get_review_result(self, student: str, week_label: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT data_hash, llm_json, model, error, analyzed_at
                FROM review_results WHERE student = ? AND week_label = ?
                """,
                (student, week_label),
            ).fetchone()
        return dict(row) if row else None

    def save_review_result(
        self,
        student: str,
        week_label: str,
        data_hash: str,
        llm_json: str | None,
        model: str | None,
        error: str | None,
        analyzed_at: str,
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO review_results
                    (student, week_label, data_hash, llm_json, model, error, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(student, week_label) DO UPDATE SET
                    data_hash=excluded.data_hash,
                    llm_json=excluded.llm_json,
                    model=excluded.model,
                    error=excluded.error,
                    analyzed_at=excluded.analyzed_at
                """,
                (student, week_label, data_hash, llm_json, model, error, analyzed_at),
            )
