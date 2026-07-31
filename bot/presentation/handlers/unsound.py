"""Обработчик /unsound — купить себе тишину: запрет на теги.

Пока запрет активен, любое сообщение с @упоминанием этого участника
удаляется (см. middlewares/unsound_guard.py). Исключение — сам бот.
"""

from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.bot_utils import format_duration, parse_duration
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="unsound")


@router.message(Command("unsound"), F.chat.type != "private")
@inject
async def cmd_unsound(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/unsound <время> — на это время никто не сможет тебя тегать."""
    if message.from_user is None or message.bot is None:
        return

    cfg = config.unsound
    p = formatter._p
    chat_id = message.chat.id
    user_id = message.from_user.id

    args = (command.args or "").strip()
    if not args:
        await reply_and_delete(
            message,
            formatter._t["unsound_usage"].format(
                cost=cfg.cost_per_minute, score_word=p.pluralize(cfg.cost_per_minute)
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    seconds = parse_duration(args)
    if seconds is None or seconds <= 0:
        await reply_and_delete(message, formatter._t["unsound_invalid"], parse_mode=ParseMode.HTML)
        return
    # Точность — до минуты: секунды не принимаем
    if seconds % 60 != 0:
        await reply_and_delete(message, formatter._t["unsound_seconds"])
        return

    minutes = seconds // 60
    if minutes < cfg.min_minutes or minutes > cfg.max_minutes:
        await reply_and_delete(
            message, formatter._t["unsound_range"].format(min=cfg.min_minutes, max=cfg.max_minutes)
        )
        return

    # Уже действует — не продлеваем и не списываем повторно
    active_until = await store.unsound_until(chat_id, user_id)
    if active_until is not None:
        await reply_and_delete(
            message,
            formatter._t["unsound_active"].format(
                remaining=format_duration(int(active_until - time.time()))
            ),
        )
        return

    cost = minutes * cfg.cost_per_minute
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=cost,
        emoji=SPECIAL_EMOJI["unsound"],
        bot_id=message.bot.id,
    )
    if not result.success:
        await reply_and_delete(
            message,
            formatter._t["unsound_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ),
        )
        return

    until_ts = time.time() + minutes * 60
    await store.unsound_set(chat_id, user_id, message.from_user.username, until_ts)
    logger.info("unsound: chat=%d user=%d minutes=%d cost=%d", chat_id, user_id, minutes, cost)

    await message.reply(
        formatter._t["unsound_success"].format(
            user=user_link(message.from_user.username, message.from_user.full_name, user_id),
            duration=format_duration(minutes * 60),
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
