"""Обработчик команды /renew — сброс игровых лимитов за кирчики."""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="renew")


@router.message(Command("renew"))
@inject
async def cmd_renew(
    message: Message,
    score_service: FromDishka[ScoreService],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Сброс лимитов слотов и блекджека за {cost} кирчиков. До {daily_limit} раз в сутки."""
    if message.from_user is None:
        return

    rc = config.renew
    p = formatter._p
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Проверка дневного лимита
    count = await store.renew_daily_count(user_id, chat_id)
    if count >= rc.daily_limit:
        await reply_and_delete(
            message,
            formatter._t["renew_daily_limit"].format(count=count, limit=rc.daily_limit),
        )
        return

    # Проверка баланса
    score = await score_service.get_score(user_id, chat_id)
    if score.value < rc.cost:
        await reply_and_delete(
            message,
            formatter._t["renew_not_enough"].format(
                cost=rc.cost,
                score_word=p.pluralize(rc.cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ),
        )
        return

    # Списываем баллы
    result = await score_service.spend_score(
        actor_id=user_id, target_id=user_id, chat_id=chat_id, cost=rc.cost,
        bot_id=message.bot.id,
    )
    if not result.success:
        await reply_and_delete(
            message,
            formatter._t["renew_not_enough"].format(
                cost=rc.cost,
                score_word=p.pluralize(rc.cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ),
        )
        return

    # Сбрасываем лимиты и фиксируем использование
    await store.renew_game_limits(user_id, chat_id)
    await store.renew_daily_increment(user_id, chat_id)

    await reply_and_delete(
        message,
        formatter._t["renew_success"].format(
            cost=rc.cost,
            score_word=p.pluralize(rc.cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
