"""Команда /unstuck — снять ограничения, оставшиеся от старых размутов бота."""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.application.mute_service import MuteService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import can_mute, lift_leftover_restriction
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="unstuck_cmd")

_SHOWN = 20  # сколько освобождённых перечислить поимённо


@router.message(Command("unstuck"))
@inject
async def cmd_unstuck(
    message: Message,
    mute_service: FromDishka[MuteService],
    tx: FromDishka[ITransactionManager],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Снимает след старого размута со всех, кого бот когда-либо мутил в чате и кто сейчас не в муте."""
    if message.from_user is None or message.bot is None:
        return
    chat_id = message.chat.id
    if not await can_mute(message.bot, chat_id, message.from_user, config.admin.users):
        await reply_and_delete(message, formatter._t["mute_not_allowed"])
        return

    candidates = await mute_service.released_users(chat_id)
    await tx.checkpoint()  # дальше только запросы к Telegram — соединение из пула не держим
    freed = [user for user in candidates if await lift_leftover_restriction(message.bot, chat_id, user.id)]

    if not freed:
        await reply_and_delete(message, formatter._t["unstuck_none"])
        return
    names = ", ".join(user_link(user.username, user.full_name, user.id) for user in freed[:_SHOWN])
    if len(freed) > _SHOWN:
        names += formatter._t["unstuck_more"].format(count=len(freed) - _SHOWN)
    await reply_and_delete(
        message,
        formatter._t["unstuck_done"].format(count=len(freed), users=names),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
