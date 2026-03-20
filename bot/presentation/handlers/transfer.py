"""Обработчик команды /transfer — перевод баллов между участниками."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import _resolve_user_and_number
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="transfer")


@router.message(Command("transfer"))
@inject
async def cmd_transfer(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None:
        return
    p = formatter._p
    chat_id = message.chat.id
    parsed = await _resolve_user_and_number(command.args, user_repo)
    if parsed is None:
        await reply_and_delete(message,formatter._t["transfer_usage"])
        return
    target, amount = parsed
    if amount <= 0:
        await reply_and_delete(message,formatter._t["transfer_invalid_amount"])
        return
    if target is None:
        await reply_and_delete(message,formatter._t["error_user_not_found"])
        return
    if target.id == message.from_user.id:
        await reply_and_delete(message,formatter._t["transfer_self"])
        return
    result = await score_service.transfer_score(
        sender_id=message.from_user.id, receiver_id=target.id, chat_id=chat_id, amount=amount
    )
    if not result.success:
        await reply_and_delete(message,
            formatter._t["transfer_not_enough"].format(
                amount=amount,
                score_word=p.pluralize(amount),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            )
        )
        return
    sender_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
    receiver_link = user_link(target.username, target.full_name, target.id)
    await reply_and_delete(message,
        formatter._t["transfer_success"].format(
            sender=sender_link,
            receiver=receiver_link,
            amount=amount,
            score_word=p.pluralize(amount),
            sender_balance=result.sender_balance,
            score_word_sender=p.pluralize(result.sender_balance),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
