"""Команда /chats — список чатов бота (только для конфиг-админов, только в ЛС)."""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.types.chat import Chat
from dishka.integrations.aiogram import FromDishka, inject
from magic_filter import F

from bot.application.interfaces.message_repository import IMessageRepository
from bot.domain.bot_utils import is_admin
from bot.infrastructure.config_loader import AppConfig

logger = logging.getLogger(__name__)
router = Router(name="admin_chats")


@router.message(Command("chats"), F.chat.type == "private")
@inject
async def cmd_chats(
    message: Message,
    bot: Bot,
    config: FromDishka[AppConfig],
    message_repo: FromDishka[IMessageRepository],
) -> None:
    if message.from_user is None:
        return

    if not is_admin(message.from_user.username, config.admin.users):
        return

    chat_ids = await message_repo.get_active_chats()
    if not chat_ids:
        await message.answer("Бот не добавлен ни в один чат.")
        return

    lines = [f"📋 <b>Чаты бота</b> ({len(chat_ids)})\n"]
    for cid in chat_ids:
        try:
            chat: Chat = await bot.get_chat(cid)
            title = chat.title or "—"
            members = chat.active_usernames
            count_str = ""
            try:
                count = await bot.get_chat_member_count(cid)
                count_str = f" · {count} уч."
            except Exception:
                pass
            lines.append(f"  • <b>{title}</b>\n    ID: <code>{cid}</code>{count_str}")
        except Exception:
            lines.append(f"  • <i>недоступен</i>\n    ID: <code>{cid}</code>")

    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
