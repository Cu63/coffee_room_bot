"""Команда /aunmute — админское снятие мута."""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.domain.bot_utils import is_admin
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import _resolve_username, _unmute_user
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="aunmute_cmd")


@router.message(Command("aunmute"))
@inject
async def cmd_aunmute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Админская команда: бесплатное снятие мута."""
    if message.from_user is None or message.bot is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message, formatter._t["admin_not_allowed"])
        return
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None:
            from bot.domain.entities import User as DomainUser

            tg = reply.from_user
            target = DomainUser(id=tg.id, username=tg.username, full_name=tg.full_name or str(tg.id))
        else:
            await reply_and_delete(message, formatter._t["aunmute_usage"])
            return
    display = user_link(target.username, target.full_name, target.id)
    chat_id = message.chat.id

    # Проверяем owner-mute (Redis soft-mute)
    if await store.owner_mute_active(chat_id, target.id):
        await store.owner_mute_delete(chat_id, target.id)
        await reply_and_delete(
            message,
            formatter._t["unmute_success"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    entry = await mute_service._repo.get(target.id, chat_id)
    if entry is None:
        await reply_and_delete(
            message,
            formatter._t["unmute_not_muted"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    await _unmute_user(message.bot, mute_service, entry)
    await reply_and_delete(
        message,
        formatter._t["unmute_success"].format(user=display),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
