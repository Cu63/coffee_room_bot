"""Команда /selfmute — самомут."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberOwner,
    ChatPermissions,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.mute_service import MuteService
from bot.domain.bot_utils import format_duration, parse_duration
from bot.domain.entities import MuteEntry
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import (
    _ADMIN_PERM_FIELDS,
    _extract_admin_permissions,
    _promote_kwargs,
)
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
            formatter._t["selfmute_usage"].format(
                min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes
            ),
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
    until, selfmute_was_stacked = await mute_service.compute_stacked_until(
        user_id, chat_id, seconds
    )
    was_admin = False
    admin_perms: dict | None = None
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if isinstance(member, ChatMemberOwner):
            until_ts = until.timestamp()
            await store.owner_mute_set(chat_id, user_id, until_ts)
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
            user_link_str = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
            await reply_and_delete(
                message,
                formatter._t["selfmute_success"].format(user=user_link_str, duration=format_duration(seconds)),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        if isinstance(member, ChatMemberAdministrator):
            was_admin = True
            admin_perms = _extract_admin_permissions(member)
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=user_id, **{f: False for f in _ADMIN_PERM_FIELDS}
            )
    except TelegramBadRequest as e:
        logger.warning("selfmute pre-check failed for user %d: %s", user_id, e)
        await reply_and_delete(message, formatter._t["selfmute_failed"])
        return
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False), until_date=until
        )
    except Exception:
        if was_admin and admin_perms:
            try:
                await bot.promote_chat_member(chat_id=chat_id, user_id=user_id, **_promote_kwargs(admin_perms))
            except Exception:
                logger.exception("Failed to restore admin rights after selfmute failure for user %d", user_id)
        await reply_and_delete(message, formatter._t["selfmute_failed"])
        return
    await mute_service.save_mute(
        MuteEntry(
            user_id=user_id,
            chat_id=chat_id,
            muted_by=user_id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        )
    )
    user_link_str = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
    await reply_and_delete(
        message,
        formatter._t["selfmute_success"].format(user=user_link_str, duration=format_duration(seconds)),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
