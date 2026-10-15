"""Команда /selfmute — самомут."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.mute_service import MuteService
from bot.domain.bot_utils import format_duration, parse_duration
from bot.domain.entities import MuteEntry
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import MuteOutcome, apply_mute
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="selfmute_cmd")


@router.message(Command("selfmute"))
@inject
async def cmd_selfmute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
    store: FromDishka[RedisStore],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    mute_cfg = config.mute
    min_sec = mute_cfg.selfmute_min_minutes * 60
    max_sec = mute_cfg.selfmute_max_minutes * 60
    if not command.args:
        await reply_and_delete(
            message,
            formatter._t["selfmute_usage"].format(min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes),
        )
        return
    seconds = parse_duration(command.args)
    if seconds is None or seconds <= 0 or seconds < min_sec or seconds > max_sec:
        await reply_and_delete(
            message,
            formatter._t["selfmute_invalid_minutes"].format(
                min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes
            ),
        )
        return
    bot = message.bot
    chat_id = message.chat.id
    user_id = message.from_user.id
    until, _ = await mute_service.compute_stacked_until(user_id, chat_id, seconds)
    muted = await apply_mute(bot, mute_service, target_id=user_id, chat_id=chat_id, muted_by=user_id, until=until)
    if muted.outcome == MuteOutcome.OWNER:
        # Владельца ограничить нельзя — мягкий мут: его сообщения удаляет middleware
        await store.owner_mute_set(chat_id, user_id, until.timestamp())
        await mute_service.save_mute(
            MuteEntry(
                user_id=user_id,
                chat_id=chat_id,
                muted_by=user_id,
                until_at=until,
                was_admin=False,
                admin_permissions=None,
            )
        )
    elif muted.outcome != MuteOutcome.MUTED:
        await reply_and_delete(message, formatter._t["selfmute_failed"])
        return
    user_link_str = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
    await reply_and_delete(
        message,
        formatter._t["selfmute_success"].format(user=user_link_str, duration=format_duration(seconds)),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
