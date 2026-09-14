from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .database import Database
from .config import Settings
from .llm import LLMError, OpenAICompatibleLLM
from .models import PlanDraft, PlanValidationError
from .sheets import SheetsError, SheetsRepository
from .weeks import is_week_label, local_today, week_label


router = Router(name="planning")


def week_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Текущая неделя", callback_data="week:current"),
                InlineKeyboardButton(text="Следующая неделя", callback_data="week:next"),
            ]
        ]
    )


def review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить и записать", callback_data="draft:confirm")],
            [
                InlineKeyboardButton(text="✏️ Изменить", callback_data="draft:edit"),
                InlineKeyboardButton(text="🔄 Заново", callback_data="draft:regenerate"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="draft:cancel")],
        ]
    )


def render_draft(draft: PlanDraft) -> str:
    lines = [f"<b>Черновик плана на {html.escape(draft.week_label)}</b>", "", "<b>Цели недели</b>"]
    lines.extend(f"{index}. {html.escape(goal)}" for index, goal in enumerate(draft.goals, 1))
    lines.extend(["", "<b>Задачи</b>"])
    for index, task in enumerate(draft.tasks, 1):
        lines.append(
            f"{index}. <b>{html.escape(task.day)}</b> · {html.escape(task.sphere)} — "
            f"{html.escape(task.display_text())}"
        )
    if draft.warnings:
        lines.extend(["", "<b>Проверь перед записью</b>"])
        lines.extend(f"• {html.escape(warning)}" for warning in draft.warnings)
    lines.extend(["", "Нажми «Подтвердить», когда план тебя устраивает."])
    return "\n".join(lines)


def registered_user(message: Message, db: Database) -> dict | None:
    return db.get_user(message.from_user.id if message.from_user else message.chat.id)


async def ask_for_registration(message: Message) -> None:
    await message.answer(
        "Сначала привяжем тебя к листу в таблице. Пришли ФИО ровно так, как оно написано в названии твоей вкладки."
    )


async def create_draft(
    message: Message,
    db: Database,
    llm: OpenAICompatibleLLM,
    source_text: str,
    week: str,
    current_draft: PlanDraft | None = None,
    original_source_text: str | None = None,
) -> None:
    await message.answer("Структурирую план. Это может занять до минуты…")
    try:
        draft = await llm.generate_plan(
            source_text=source_text,
            week_label=week,
            current_draft=current_draft,
            correction=source_text if current_draft else None,
            session_id=f"telegram-{message.from_user.id}",
        )
    except (LLMError, PlanValidationError) as exc:
        await message.answer(f"Не получилось собрать черновик: {html.escape(str(exc))}")
        return
    db.save_draft(
        message.from_user.id,
        week,
        draft.to_dict(),
        original_source_text or source_text,
    )
    await message.answer(render_draft(draft), reply_markup=review_keyboard())


@router.message(CommandStart())
async def start(message: Message, db: Database) -> None:
    user = registered_user(message, db)
    if user:
        await message.answer(
            f"Привет, {html.escape(user['display_name'])}!\n\n"
            "Напиши /plan, чтобы создать план, или сразу пришли описание недели."
        )
        return
    await message.answer(
        "Привет! Я превращаю свободное описание недели в план и после твоего подтверждения записываю его в Google Sheets."
    )
    await ask_for_registration(message)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Команды:\n"
        "/plan — выбрать неделю и описать планы\n"
        "/cancel — отменить текущий черновик\n"
        "/help — показать эту справку\n\n"
        "После черновика можно нажать «Изменить» и написать правки обычным текстом."
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message, db: Database) -> None:
    db.clear_session(message.from_user.id)
    await message.answer("Текущий черновик отменён.")


@router.message(Command("plan"))
async def plan_command(message: Message, db: Database, settings: Settings) -> None:
    if not registered_user(message, db):
        await ask_for_registration(message)
        return
    argument = (message.text or "").partition(" ")[2].strip()
    if argument in {"текущую", "сейчас", "current"}:
        week = week_label(local_today(settings.app_timezone))
        db.set_session(message.from_user.id, "input", week_label=week)
        await message.answer(f"Понял. Напиши планы на {week} одним сообщением.")
        return
    if argument in {"следующую", "next"}:
        week = week_label(local_today(settings.app_timezone), 1)
        db.set_session(message.from_user.id, "input", week_label=week)
        await message.answer(f"Понял. Напиши планы на {week} одним сообщением.")
        return
    if argument and is_week_label(argument):
        db.set_session(message.from_user.id, "input", week_label=argument)
        await message.answer(f"Понял. Напиши планы на {argument} одним сообщением.")
        return
    await message.answer("На какую неделю планируем?", reply_markup=week_keyboard())


@router.callback_query(F.data.in_({"week:current", "week:next"}))
async def choose_week(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    user_id = callback.from_user.id
    offset = 1 if callback.data == "week:next" else 0
    week = week_label(local_today(settings.app_timezone), offset)
    db.set_session(user_id, "input", week_label=week)
    await callback.answer()
    if callback.message:
        await callback.message.answer(f"Напиши примерные планы на {week} одним сообщением.")


@router.callback_query(F.data == "draft:edit")
async def edit_draft(callback: CallbackQuery, db: Database) -> None:
    db.set_edit_mode(callback.from_user.id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Напиши, что изменить. Например: «Перенеси тренировку на субботу, добавь 60 минут и убери задачу про отчёт»."
        )


@router.callback_query(F.data == "draft:regenerate")
async def regenerate_draft(
    callback: CallbackQuery, db: Database, llm: OpenAICompatibleLLM
) -> None:
    session = db.get_session(callback.from_user.id)
    if not session or not session.get("payload"):
        await callback.answer("Черновик уже отсутствует", show_alert=True)
        return
    await callback.answer("Собираю заново…")
    if not callback.message:
        return
    try:
        current = PlanDraft.from_json(session["payload"])
        draft = await llm.generate_plan(
            session.get("source_text") or "",
            current.week_label,
            session_id=f"telegram-{callback.from_user.id}",
        )
    except (LLMError, PlanValidationError) as exc:
        await callback.message.answer(f"Не получилось собрать заново: {html.escape(str(exc))}")
        return
    db.save_draft(callback.from_user.id, current.week_label, draft.to_dict(), session.get("source_text") or "")
    await callback.message.answer(render_draft(draft), reply_markup=review_keyboard())


@router.callback_query(F.data == "draft:cancel")
async def cancel_draft(callback: CallbackQuery, db: Database) -> None:
    db.clear_session(callback.from_user.id)
    await callback.answer("Черновик отменён")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Хорошо, ничего не записал.")


@router.callback_query(F.data == "draft:confirm")
async def confirm_draft(
    callback: CallbackQuery, db: Database, sheets: SheetsRepository
) -> None:
    user = db.get_user(callback.from_user.id)
    session = db.claim_draft(callback.from_user.id)
    if not user or not session or not session.get("payload"):
        await callback.answer("Черновик уже отсутствует", show_alert=True)
        return
    try:
        draft = PlanDraft.from_json(session["payload"])
        start_row = sheets.append_plan(user["sheet_name"], draft)
    except (PlanValidationError, KeyError, RuntimeError, ValueError, SheetsError) as exc:
        if session.get("payload"):
            db.set_session(
                callback.from_user.id,
                phase="review",
                week_label=session.get("week_label"),
                payload=session.get("payload"),
                source_text=session.get("source_text"),
            )
        await callback.answer("Не удалось записать", show_alert=True)
        if callback.message:
            await callback.message.answer(
                "Не удалось записать план в таблицу. Проверь, что сервисный аккаунт имеет доступ редактора.\n"
                f"Детали: {html.escape(str(exc))}"
            )
        return
    db.clear_session(callback.from_user.id)
    await callback.answer("Записано")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"План записан в лист «{html.escape(user['sheet_name'])}», начиная со строки {start_row}.\n"
            f"<a href=\"{sheets.spreadsheet_url()}\">Открыть таблицу</a>"
        )


@router.message(F.text)
async def text_message(
    message: Message,
    db: Database,
    sheets: SheetsRepository,
    llm: OpenAICompatibleLLM,
    settings: Settings,
) -> None:
    user = registered_user(message, db)
    if not user:
        try:
            student = sheets.find_student((message.text or "").strip())
        except SheetsError as exc:
            await message.answer(
                "Не могу прочитать названия вкладок в Google Sheets. "
                f"Проверь доступ сервисного аккаунта. Детали: {html.escape(str(exc))}"
            )
            return
        if student:
            db.register_user(message.from_user.id, student.display_name, student.sheet_name)
            await message.answer(
                f"Готово, привязал тебя к листу «{html.escape(student.sheet_name)}».\n"
                "Теперь напиши /plan или сразу отправь описание планов на текущую неделю."
            )
        else:
            await message.answer(
                "Не нашёл вкладку с таким названием. Пришли ФИО ровно как оно написано в названии листа или используй /start."
            )
        return

    text = (message.text or "").strip()
    session = db.get_session(message.from_user.id)
    if not session:
        session = {"phase": "input", "week_label": week_label(local_today(settings.app_timezone))}
        db.set_session(message.from_user.id, "input", week_label=session["week_label"])

    phase = session.get("phase")
    if phase == "input":
        await create_draft(message, db, llm, text, session["week_label"])
        return
    if phase in {"edit", "review"} and session.get("payload"):
        try:
            current = PlanDraft.from_json(session["payload"])
        except (PlanValidationError, ValueError) as exc:
            await message.answer(f"Черновик повреждён: {html.escape(str(exc))}. Начни заново через /plan.")
            db.clear_session(message.from_user.id)
            return
        await create_draft(
            message,
            db,
            llm,
            text,
            current.week_label,
            current,
            original_source_text=session.get("source_text") or text,
        )
        return
    await message.answer("Выбери неделю через /plan и пришли описание планов.")
