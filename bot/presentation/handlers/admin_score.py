"""Админские команды управления баллами: /reset, /set, /add, /sub."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.bot_utils import is_admin
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.handlers._admin_utils import (
    _admin_reply,
    _resolve_user_and_number,
    _resolve_username,
)
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="admin_score")


@router.message(Command("reset"))
@inject
async def cmd_reset(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    if not command.args:
        await reply_and_delete(message,formatter._t["admin_usage_reset"])
        return
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        await reply_and_delete(message,formatter._t["error_user_not_found"])
        return
    new_value = await score_service.set_score(target.id, message.chat.id, 0, admin_id=message.from_user.id)
    await reply_and_delete(message,
        _admin_reply(formatter, target, new_value), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


@router.message(Command("set"))
@inject
async def cmd_set(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    parsed = await _resolve_user_and_number(command.args, user_repo)
    if parsed is None or parsed[0] is None:
        await reply_and_delete(message,formatter._t["admin_usage_set"])
        return
    target, amount = parsed
    if target is None:
        await reply_and_delete(message,formatter._t["error_user_not_found"])
        return
    new_value = await score_service.set_score(target.id, message.chat.id, amount, admin_id=message.from_user.id)
    await reply_and_delete(message,
        _admin_reply(formatter, target, new_value), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


@router.message(Command("add"))
@inject
async def cmd_add(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    parsed = await _resolve_user_and_number(command.args, user_repo)
    if parsed is None:
        await reply_and_delete(message,formatter._t["admin_usage_add"])
        return
    target, amount = parsed
    if target is None:
        await reply_and_delete(message,formatter._t["error_user_not_found"])
        return
    if amount <= 0:
        await reply_and_delete(message,formatter._t["admin_negative_amount"])
        return
    new_value = await score_service.add_score(target.id, message.chat.id, amount, admin_id=message.from_user.id)
    await reply_and_delete(message,
        _admin_reply(formatter, target, new_value), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


@router.message(Command("sub"))
@inject
async def cmd_sub(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message,formatter._t["admin_not_allowed"])
        return
    parsed = await _resolve_user_and_number(command.args, user_repo)
    if parsed is None:
        await reply_and_delete(message,formatter._t["admin_usage_sub"])
        return
    target, amount = parsed
    if target is None:
        await reply_and_delete(message,formatter._t["error_user_not_found"])
        return
    if amount <= 0:
        await reply_and_delete(message,formatter._t["admin_negative_amount"])
        return
    new_value = await score_service.add_score(target.id, message.chat.id, -amount, admin_id=message.from_user.id)
    await reply_and_delete(message,
        _admin_reply(formatter, target, new_value), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )
