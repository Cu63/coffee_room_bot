"""Команда /unmute (она же /aunmute) — снятие мута тем, у кого есть право на муты."""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.domain.entities import User
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import _resolve_username, can_mute, lift_leftover_restriction, lift_mute
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="unmute_cmd")


@router.message(Command("unmute", "aunmute"))
@inject
async def cmd_unmute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/unmute @user или ответом на сообщение — снять мут досрочно и вернуть штраф за него."""
    if message.from_user is None or message.bot is None:
        return
    chat_id = message.chat.id
    # Иначе мут модератора снимал бы кто угодно
    if not await can_mute(message.bot, chat_id, message.from_user, config.admin.users):
        await reply_and_delete(message, formatter._t["mute_not_allowed"])
        return

    target = await _resolve_username(command.args, user_repo)
    if target is None:
        reply = message.reply_to_message
        if reply is None or reply.from_user is None:
            await reply_and_delete(message, formatter._t["unmute_usage"])
            return
        tg = reply.from_user
        target = User(id=tg.id, username=tg.username, full_name=tg.full_name or str(tg.id))
    display = user_link(target.username, target.full_name, target.id)

    owner_muted = await store.owner_mute_active(chat_id, target.id)
    entry = await mute_service.get_mute(target.id, chat_id)
    # Мута бота нет — но мог остаться след старого размута: его снимаем, а ручное ограничение не трогаем
    if not owner_muted and entry is None and not await lift_leftover_restriction(message.bot, chat_id, target.id):
        await reply_and_delete(
            message,
            formatter._t["unmute_not_muted"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # Штраф достаётся тому, чьё снятие удалило запись: из двух одновременных /unmute вернёт один
    fine = 0
    if owner_muted:
        # Владельца Telegram не ограничивает: его мут — удаление сообщений по записи в Redis
        await store.owner_mute_delete(chat_id, target.id)
        if entry is not None:
            fine = await mute_service.delete_mute(target.id, chat_id)
    elif entry is not None:
        fine = await lift_mute(message.bot, mute_service, entry)
    refunded = await mute_service.refund_fine(target.id, chat_id, fine, bot_id=message.bot.id)

    text = formatter._t["unmute_success"].format(user=display)
    if fine and refunded == fine:
        text += "\n" + formatter._t["unmute_refund"].format(amount=formatter.amount(fine))
    elif fine:
        text += "\n" + formatter._t["unmute_refund_partial"].format(
            amount=formatter.amount(refunded), fine=formatter.amount(fine)
        )
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
