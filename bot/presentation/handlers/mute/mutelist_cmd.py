"""Команда /mutelist — кто сейчас в муте и сколько осталось."""

from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.mute_service import MuteService
from bot.domain.bot_utils import format_duration
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import can_mute
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="mutelist_cmd")

_SHOWN = 50  # строк в одном сообщении


@router.message(Command("mutelist"))
@inject
async def cmd_mutelist(
    message: Message,
    mute_service: FromDishka[MuteService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Действующие муты чата: кто, сколько осталось и кто выдал. Для тех, у кого есть право на муты."""
    if message.from_user is None or message.bot is None:
        return
    chat_id = message.chat.id
    if not await can_mute(message.bot, chat_id, message.from_user, config.admin.users):
        await reply_and_delete(message, formatter._t["mute_not_allowed"])
        return

    mutes = await mute_service.active_in_chat(chat_id)
    if not mutes:
        await reply_and_delete(message, formatter._t["mutelist_none"])
        return
    now = datetime.now(TZ_MSK)
    lines = [formatter._t["mutelist_header"].format(count=len(mutes))]
    for mute in mutes[:_SHOWN]:
        if mute.muted_by is None:
            by = "—"
        elif mute.muted_by.id == mute.user.id:
            by = formatter._t["mutelist_self"]
        else:
            by = user_link(mute.muted_by.username, mute.muted_by.full_name, mute.muted_by.id)
        lines.append(
            formatter._t["mutelist_row"].format(
                target=user_link(mute.user.username, mute.user.full_name, mute.user.id),
                left=format_duration(max(1, int((mute.until_at - now).total_seconds()))),
                by=by,
            )
        )
    await reply_and_delete(message, "\n".join(lines), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
