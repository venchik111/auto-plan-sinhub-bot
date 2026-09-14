from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Подключить аккаунт Skyeng")
    parser.add_argument("--storage-state", type=Path)
    parser.add_argument(
        "--auto",
        action="store_true",
        help="завершить автоматически после успешной загрузки расписания",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    target = args.storage_state or settings.skyeng_storage_state_file
    target.parent.mkdir(parents=True, exist_ok=True)
    executable = os.getenv("BROWSER_EXECUTABLE", "/usr/bin/chromium")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, executable_path=executable)
        context = browser.new_context()
        page = context.new_page()
        authenticated = False

        def on_response(response) -> None:
            nonlocal authenticated
            if (
                response.status == 200
                and "/api/v1/college-student-cabinet/timetable/weekly" in response.url
            ):
                authenticated = True

        page.on("response", on_response)
        page.goto(
            "https://id.skyeng.ru/login?redirect=https%3A%2F%2Favatar.skyeng.ru%2Fstudent%2Fschedule",
            wait_until="domcontentloaded",
        )
        print("Войди в Skyeng в открывшемся окне.", flush=True)
        try:
            if args.auto:
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline and not authenticated:
                    page.wait_for_timeout(500)
                if not authenticated:
                    print("Не дождался успешной авторизации Skyeng.", flush=True)
                    return 2
            else:
                input("Когда расписание появится, нажми Enter здесь: ")
        except PlaywrightError:
            print("Окно входа Skyeng закрыли до завершения авторизации.", flush=True)
            return 2
        context.storage_state(path=str(target))
        os.chmod(target, 0o600)
        browser.close()
    print(f"Авторизация сохранена в {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
