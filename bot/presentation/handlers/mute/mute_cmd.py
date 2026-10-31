"""Команда /mute (она же /amute) — мут участника тем, у кого есть право на муты."""

from __future__ import annotations

import logging
import math
from datetime import datetime

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberRestricted,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.domain.bot_utils import parse_duration
from bot.domain.entities import User
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import MuteOutcome, _resolve_mute_args, apply_mute, can_mute
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="mute_cmd")

_RANDOM_TARGETS = {"random", "@random"}
_RANDOM_PICK_ATTEMPTS = 5


async def _pick_random_member(bot: Bot, score_repo: IScoreRepository, chat_id: int, exclude_id: int) -> User | None:
    """Случайный участник с ненулевым счётом в чате (не бот, не exclude_id), который ещё в чате.

    Счёт остаётся и у тех, кто вышел: мутить отсутствующего незачем, поэтому
    таких пропускаем и тянем ещё раз.
    """
    for _ in range(_RANDOM_PICK_ATTEMPTS):
        row = await score_repo.get_random_user(chat_id, exclude_id)
        if row is None:
            return None
        user_id, username, full_name = row
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramAPIError:
            logger.warning("mute random: не удалось проверить участника %d в чате %d", user_id, chat_id)
            continue
        if isinstance(member, ChatMemberLeft | ChatMemberBanned):
            continue
        if isinstance(member, ChatMemberRestricted) and not member.is_member:
            continue
        return User(id=user_id, username=username, full_name=full_name)
    return None


@router.message(Command("mute", "amute"))
@inject
async def cmd_mute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    score_repo: FromDishka[IScoreRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/mute @user <время>, ответом /mute <время> или /mute random <время>. Бесплатно."""
    if message.from_user is None or message.bot is None:
        return
    bot = message.bot
    chat_id = message.chat.id
    # Мутить других может только тот, у кого есть право на муты
    if not await can_mute(bot, chat_id, message.from_user, config.admin.users):
        await reply_and_delete(message, formatter._t["mute_not_allowed"])
        return
    usage = formatter._t["mute_usage"]
    args = (command.args or "").split()
    # /mute random <время> — цель выбирает бот
    is_random = len(args) == 2 and args[0].lower() in _RANDOM_TARGETS
    if is_random:
        seconds = parse_duration(args[1])
        if seconds is None:
            await reply_and_delete(message, usage)
            return
        minutes = max(1, seconds // 60)
        target = await _pick_random_member(bot, score_repo, chat_id, message.from_user.id)
        if target is None:
            await reply_and_delete(message, formatter._t["mute_random_no_candidates"])
            return
    else:
        parsed = await _resolve_mute_args(command.args, message, user_repo)
        if parsed is None:
            await reply_and_delete(message, usage)
            return
        target, minutes = parsed
        if target is None:
            await reply_and_delete(message, formatter._t["error_user_not_found"])
            return
    if target.id == message.from_user.id:
        await reply_and_delete(message, formatter._t["mute_self"])
        return

    # Стекуем: прибавляем к оставшемуся времени мута, не заменяем его
    until, was_stacked = await mute_service.compute_stacked_until(target.id, chat_id, minutes * 60)
    muted = await apply_mute(
        bot, mute_service, target_id=target.id, chat_id=chat_id, muted_by=message.from_user.id, until=until
    )
    target_link = user_link(target.username, target.full_name, target.id)
    if muted.outcome == MuteOutcome.OWNER:
        await reply_and_delete(message, formatter._t["mute_owner"])
        return
    if muted.outcome == MuteOutcome.RESTRICTED_BY_ADMIN:
        await reply_and_delete(
            message,
            formatter._t["mute_restricted_by_admin"].format(target=target_link),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    if muted.outcome != MuteOutcome.MUTED:
        await reply_and_delete(message, formatter._t["mute_failed"])
        return

    until = muted.until or until
    actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
    total_minutes = math.ceil((until - datetime.now(TZ_MSK)).total_seconds() / 60)
    stack_note = f" (итого: {total_minutes} мин)" if was_stacked else ""
    random_note = formatter._t["mute_random_picked"].format(target=target_link) + "\n" if is_random else ""
    await reply_and_delete(
        message,
        random_note
        + formatter._t["mute_success"].format(actor=actor_link, target=target_link, minutes=minutes)
        + stack_note,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
