"""Хендлер /donotbuy — «кнопка наёбка».

Поток:
  1. /donotbuy → бот шлёт почти пустое сообщение с одной кнопкой «кнопка наёбка».
  2. При нажатии: с игрока списывается случайная цена (0..max_price),
     баланс не уходит в минус, кнопка меняет цвет (style).
  3. Сколько списалось — видит только нажавший (alert).
  4. Лимит нажатий в сутки на пользователя (в т.ч. неудачных).
"""

from __future__ import annotations

import logging
import random

from aiogram import F, Router
from dishka.integrations.aiogram import FromDishka, inject
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.score_service import ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import safe_callback_answer

logger = logging.getLogger(__name__)
router = Router(name="donotbuy")

# Цвета кнопки через aiogram InlineKeyboardButton.style:
# 'danger' (красный), 'success' (зелёный), 'primary' (синий), None — дефолт.
_STYLES = ["danger", "success", "primary", None]

_CB_HIT = "donotbuy:hit"


def _build_kb(label: str, style: str | None) -> InlineKeyboardMarkup:
    """Одна кнопка «кнопка наёбка» заданного цвета (style)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=_CB_HIT, style=style)]
        ]
    )


@router.message(Command("donotbuy"), F.chat.type.in_({"group", "supergroup"}))
@inject
async def cmd_donotbuy(
    message: Message,
    config: FromDishka[AppConfig],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None or message.bot is None:
        return

    cfg = config.donotbuy
    if not cfg.enabled:
        await message.answer(formatter._t["donotbuy_disabled"])
        return

    await message.answer(
        formatter._t["donotbuy_title"],
        reply_markup=_build_kb(formatter._t["donotbuy_button"], random.choice(_STYLES)),
    )
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data == _CB_HIT)
@inject
async def cb_donotbuy_hit(
    callback: CallbackQuery,
    config: FromDishka[AppConfig],
    formatter: FromDishka[MessageFormatter],
    score_service: FromDishka[ScoreService],
    score_repo: FromDishka[IScoreRepository],
    store: FromDishka[RedisStore],
) -> None:
    if callback.message is None or callback.bot is None:
        await safe_callback_answer(callback)
        return

    cfg = config.donotbuy
    p = formatter._p
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id

    # Лимит нажатий в сутки (в т.ч. неудачных)
    count = await store.donotbuy_daily_count(user_id, chat_id)
    if count >= cfg.daily_limit:
        await safe_callback_answer(
            callback,
            formatter._t["donotbuy_alert_limit"].format(limit=cfg.daily_limit),
            show_alert=True,
        )
        return

    # Любое нажатие — успешное или нет — расходует лимит
    await store.donotbuy_daily_increment(user_id, chat_id)

    # Случайная цена и списание без ухода в минус
    price = random.randint(0, cfg.max_price)
    balance = (await score_service.get_score(user_id, chat_id)).value
    charged = min(price, balance) if balance > 0 else 0

    if charged > 0:
        await score_repo.add_delta(user_id, chat_id, -charged)
        await score_repo.add_delta(callback.bot.id, chat_id, charged)

    # Кнопка меняет цвет — обновляем только клавиатуру (текст сообщения не трогаем)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_build_kb(formatter._t["donotbuy_button"], random.choice(_STYLES)),
        )
    except Exception:
        pass

    # Сколько списалось — видит только нажавший
    if charged > 0:
        alert = formatter._t["donotbuy_alert_charged"].format(
            amount=charged, score_word=p.pluralize(charged),
        )
    else:
        alert = formatter._t["donotbuy_alert_zero"]
    await safe_callback_answer(callback, alert, show_alert=True)
