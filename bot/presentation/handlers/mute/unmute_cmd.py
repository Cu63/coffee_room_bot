"""Команда /unmute — платное снятие мута."""

from __future__ import annotations

import math
from datetime import datetime

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.application.score_service import ScoreService
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import _resolve_username, _unmute_user
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="unmute_cmd")


@router.message(Command("unmute"))
@inject
async def cmd_unmute(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Платное снятие мута с другого пользователя за кирчики."""
    if message.from_user is None or message.bot is None:
        return

    # Резолвим цель: @username или reply
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None:
            from bot.domain.entities import User as DomainUser

            tg = reply.from_user
            target = DomainUser(id=tg.id, username=tg.username, full_name=tg.full_name or str(tg.id))
        else:
            await reply_and_delete(message, formatter._t["unmute_usage"])
            return

    actor_id = message.from_user.id
    target_id = target.id
    chat_id = message.chat.id
    mute_cfg = config.mute
    p = formatter._p
    target_display = user_link(target.username, target.full_name, target_id)
    actor_display = user_link(message.from_user.username, message.from_user.full_name or "", actor_id)

    # Проверяем, замутён ли target
    is_owner_muted = await store.owner_mute_active(chat_id, target_id)
    entry = await mute_service._repo.get(target_id, chat_id)

    if not is_owner_muted and entry is None:
        await reply_and_delete(
            message,
            formatter._t["unmute_not_muted"].format(user=target_display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # Вычисляем оставшееся время и стоимость
    now = datetime.now(TZ_MSK)
    if is_owner_muted:
        raw_ts = await store.owner_mute_get_ts(chat_id, target_id)
        remaining_minutes = max((raw_ts - now.timestamp()) / 60, 1) if raw_ts else 1
    else:
        remaining_minutes = max((entry.until_at - now).total_seconds() / 60, 1)

    cost = max(1, math.ceil(remaining_minutes * mute_cfg.cost_per_minute * mute_cfg.unmute_multiplier))

    score = await score_service.get_score(actor_id, chat_id)
    if score.value < cost:
        await reply_and_delete(
            message,
            formatter._t["unmute_not_enough"].format(
                target=target_display,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
                minutes=math.ceil(remaining_minutes),
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # Снимаем мут
    if is_owner_muted:
        await store.owner_mute_delete(chat_id, target_id)
    if entry is not None:
        await _unmute_user(message.bot, mute_service, entry)

    # Списываем баллы с актора
    await score_service.add_score(actor_id, chat_id, -cost, admin_id=actor_id)
    new_balance = score.value - cost

    await reply_and_delete(
        message,
        formatter._t["unmute_paid_success"].format(
            actor=actor_display,
            target=target_display,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=new_balance,
            score_word_balance=p.pluralize(new_balance),
            minutes=math.ceil(remaining_minutes),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
