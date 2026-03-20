"""Админские команды управления правами: /save, /restore, /op, /deop."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.saved_permissions_repository import ISavedPermissionsRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.bot_utils import is_admin
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import (
    _ADMIN_PERM_FIELDS,
    MODERATOR_PERMS,
    _extract_admin_permissions,
    _promote_kwargs,
    _resolve_username,
)
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="admin_user")


@router.message(Command("save"))
@inject
async def cmd_save(
    message: Message,
    command: CommandObject,
    user_repo: FromDishka[IUserRepository],
    saved_perms_repo: FromDishka[ISavedPermissionsRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        await reply_and_delete(message,formatter._t["save_usage"])
        return
    display = user_link(target.username, target.full_name, target.id)
    try:
        member = await message.bot.get_chat_member(message.chat.id, target.id)
    except Exception:
        await reply_and_delete(message,
            formatter._t["save_not_admin"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    if not isinstance(member, (ChatMemberAdministrator, ChatMemberOwner)):
        await reply_and_delete(message,
            formatter._t["save_not_admin"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    if isinstance(member, ChatMemberOwner):
        perms = {field: True for field in _ADMIN_PERM_FIELDS}
        if member.custom_title:
            perms["custom_title"] = member.custom_title
    else:
        perms = _extract_admin_permissions(member)
    existing = await saved_perms_repo.get(target.id, message.chat.id)
    await saved_perms_repo.save(target.id, message.chat.id, perms)
    key = "save_overwritten" if existing else "save_success"
    await reply_and_delete(message,
        formatter._t[key].format(user=display), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


@router.message(Command("restore"))
@inject
async def cmd_restore(
    message: Message,
    command: CommandObject,
    user_repo: FromDishka[IUserRepository],
    saved_perms_repo: FromDishka[ISavedPermissionsRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        await reply_and_delete(message,formatter._t["restore_usage"])
        return
    display = user_link(target.username, target.full_name, target.id)
    perms = await saved_perms_repo.get(target.id, message.chat.id)
    if perms is None:
        await reply_and_delete(message,
            formatter._t["restore_not_found"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    try:
        await message.bot.promote_chat_member(chat_id=message.chat.id, user_id=target.id, **_promote_kwargs(perms))
        if perms.get("custom_title"):
            await message.bot.set_chat_administrator_custom_title(
                chat_id=message.chat.id, user_id=target.id, custom_title=perms["custom_title"]
            )
    except Exception:
        logger.exception("Failed to restore permissions for user %d", target.id)
        await reply_and_delete(message,formatter._t["restore_failed"])
        return
    await reply_and_delete(message,
        formatter._t["restore_success"].format(user=display),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("op"))
@inject
async def cmd_op(
    message: Message,
    command: CommandObject,
    user_repo: FromDishka[IUserRepository],
    saved_perms_repo: FromDishka[ISavedPermissionsRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        await reply_and_delete(message,formatter._t["op_usage"])
        return
    display = user_link(target.username, target.full_name, target.id)
    try:
        member = await message.bot.get_chat_member(message.chat.id, target.id)
    except Exception:
        await reply_and_delete(message,formatter._t["op_failed"])
        return
    if isinstance(member, (ChatMemberOwner, ChatMemberAdministrator)):
        await reply_and_delete(message,
            formatter._t["op_already"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    try:
        await message.bot.promote_chat_member(
            chat_id=message.chat.id, user_id=target.id, **MODERATOR_PERMS
        )
    except Exception:
        logger.exception("Failed to op user %d", target.id)
        await reply_and_delete(message,formatter._t["op_failed"])
        return
    await saved_perms_repo.save(target.id, message.chat.id, MODERATOR_PERMS)
    await reply_and_delete(message,
        formatter._t["op_success"].format(user=display),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("deop"))
@inject
async def cmd_deop(
    message: Message,
    command: CommandObject,
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        await reply_and_delete(message,formatter._t["op_usage"])
        return
    display = user_link(target.username, target.full_name, target.id)
    try:
        member = await message.bot.get_chat_member(message.chat.id, target.id)
    except Exception:
        await reply_and_delete(message,formatter._t["op_failed"])
        return
    if isinstance(member, ChatMemberOwner):
        await reply_and_delete(message,
            formatter._t["op_already"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    if not isinstance(member, ChatMemberAdministrator):
        await reply_and_delete(message,
            formatter._t["deop_not_admin"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    try:
        demote_kw = {f: False for f in _ADMIN_PERM_FIELDS}
        await message.bot.promote_chat_member(chat_id=message.chat.id, user_id=target.id, **demote_kw)
    except Exception:
        logger.exception("Failed to deop user %d", target.id)
        await reply_and_delete(message,formatter._t["op_failed"])
        return
    await reply_and_delete(message,
        formatter._t["deop_success"].format(user=display),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
