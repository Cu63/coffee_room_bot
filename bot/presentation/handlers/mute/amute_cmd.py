"""Команда /amute — бесплатный мут для администраторов."""

from __future__ import annotations

import logging
import math
from datetime import datetime

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberOwner,
    ChatPermissions,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.domain.bot_utils import is_admin
from bot.domain.entities import MuteEntry
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import (
    _ADMIN_PERM_FIELDS,
    _extract_admin_permissions,
    _promote_kwargs,
    _resolve_mute_args,
)
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="amute_cmd")


@router.message(Command("amute"))
@inject
async def cmd_amute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Бесплатный мут для администраторов, обходит /protect."""
    if message.from_user is None or message.bot is None:
        return
    bot = message.bot
    chat_id = message.chat.id
    mute_cfg = config.mute
    is_config_admin = is_admin(message.from_user.username, config.admin.users)
    if not is_config_admin:
        try:
            caller_member = await bot.get_chat_member(chat_id, message.from_user.id)
            has_restrict = (
                isinstance(caller_member, ChatMemberAdministrator) and caller_member.can_restrict_members
            ) or isinstance(caller_member, ChatMemberOwner)
        except Exception:
            has_restrict = False
        if not has_restrict:
            await reply_and_delete(message, formatter._t["amute_not_allowed"])
            return
    parsed = await _resolve_mute_args(command.args, message, user_repo)
    if parsed is None:
        await reply_and_delete(message, formatter._t["amute_usage"].format(min=mute_cfg.min_minutes, max=mute_cfg.max_minutes))
        return
    target, minutes = parsed
    if target is None:
        await reply_and_delete(message, formatter._t["error_user_not_found"])
        return
    if target.id == message.from_user.id:
        await reply_and_delete(message, formatter._t["mute_self"])
        return
    until, amute_was_stacked = await mute_service.compute_stacked_until(
        target.id, chat_id, minutes * 60
    )
    try:
        member = await bot.get_chat_member(chat_id, target.id)
    except Exception:
        await reply_and_delete(message, formatter._t["mute_failed"])
        return
    was_admin = isinstance(member, ChatMemberAdministrator)
    admin_perms: dict | None = None
    if was_admin:
        admin_perms = _extract_admin_permissions(member)
        try:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=target.id, **{f: False for f in _ADMIN_PERM_FIELDS}
            )
        except Exception:
            await reply_and_delete(message, formatter._t["mute_failed"])
            return
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception:
        if was_admin and admin_perms:
            try:
                await bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **_promote_kwargs(admin_perms))
            except Exception:
                logger.exception("Failed to restore admin rights after amute failure")
        await reply_and_delete(message, formatter._t["mute_failed"])
        return
    await mute_service.save_mute(
        MuteEntry(
            user_id=target.id,
            chat_id=chat_id,
            muted_by=message.from_user.id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        )
    )
<<<<<<< HEAD
    await mute_service.log_mute(target.id, message.from_user.id, chat_id)
=======
>>>>>>> origin/master
    actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
    target_link = user_link(target.username, target.full_name, target.id)
    amute_total_minutes = math.ceil((until - datetime.now(TZ_MSK)).total_seconds() / 60)
    amute_stack_note = f" (итого: {amute_total_minutes} мин)" if amute_was_stacked else ""
    await reply_and_delete(
        message,
        formatter._t["amute_success"].format(actor=actor_link, target=target_link, minutes=minutes) + amute_stack_note,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
