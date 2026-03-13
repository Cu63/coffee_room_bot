"""Обработчики команды /help и связанных callback-кнопок."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.handlers.help_renderer import HelpRenderer
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="help")


@router.message(Command("help"))
@inject
async def cmd_help(
    message: Message,
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
    renderer: FromDishka[HelpRenderer],
) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id
    await reply_and_delete(
        message,
        renderer.main_text(config.score.icon),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=renderer.main_kb(uid),
    )


# Callback для /help. Формат callback_data: "help:{section}:{caller_uid}"

@router.callback_query(F.data.startswith("help:"))
@inject
async def cb_help(
    callback: CallbackQuery,
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
    renderer: FromDishka[HelpRenderer],
) -> None:
    async def safe_answer(text: str = "", show_alert: bool = False) -> None:
        try:
            await callback.answer(text, show_alert=show_alert)
        except Exception:
            pass

    parts = callback.data.split(":")
    if len(parts) != 3:
        await safe_answer()
        return

    _, section, caller_uid_str = parts
    try:
        caller_uid = int(caller_uid_str)
    except ValueError:
        await safe_answer()
        return

    if callback.from_user.id != caller_uid:
        await safe_answer("Это не твоя справка.", show_alert=True)
        return

    uid = caller_uid
    if section == "main":
        text = renderer.main_text(config.score.icon)
        kb = renderer.main_kb(uid)
    else:
        text = renderer.section_text(section, config, formatter)
        kb = renderer.back_kb(uid)

    if not text:
        await safe_answer()
        return

    try:
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, link_preview_options=NO_PREVIEW
        )
    except Exception:
        pass

    await safe_answer()
