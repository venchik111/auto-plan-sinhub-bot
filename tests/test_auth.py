from pathlib import Path

from app.auth import hash_password, normalize_login, verify_password
from app.database import Database


def test_password_hash_and_login_normalization() -> None:
    encoded = hash_password("secret123")

    assert verify_password("secret123", encoded)
    assert not verify_password("wrong", encoded)
    assert normalize_login("  Ivan.Petrov ") == "ivan.petrov"


def test_account_requires_approval_and_remembers_profile(tmp_path: Path) -> None:
    database = Database(tmp_path / "accounts.sqlite3")
    database.init()
    account = database.create_account(
        "ivan",
        hash_password("secret123"),
        "Иван Петров",
        "Иван Петров",
    )

    assert account["status"] == "pending"
    assert database.get_account_by_session("session-token") is None

    database.set_account_status(account["account_id"], "approved")
    database.create_account_session("session-token", account["account_id"])
    database.update_account_profile(
        account["account_id"], {"guiding_answers": {"goal": "Закрыть модуль"}}
    )

    saved = database.get_account_by_session("session-token")
    assert saved is not None
    assert saved["profile"]["guiding_answers"]["goal"] == "Закрыть модуль"
