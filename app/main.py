from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .bot import router
from .config import Settings
from .database import Database
from .llm import OpenAICompatibleLLM
from .sheets import SheetsRepository


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise RuntimeError("Для Telegram-бота не задана переменная TELEGRAM_BOT_TOKEN")
    database = Database(settings.database_path)
    database.init()
    sheets = SheetsRepository.from_settings(settings)
    llm = OpenAICompatibleLLM(settings)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher["db"] = database
    dispatcher["sheets"] = sheets
    dispatcher["llm"] = llm
    dispatcher["settings"] = settings
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
