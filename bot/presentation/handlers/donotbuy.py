"""Хендлер /donotbuy — «кнопка наёбка».

Поток:
  1. /donotbuy → бот рисует сетку, в случайной ячейке стоит «кнопка наёбка».
  2. При нажатии: с игрока списывается случайная цена (0..max_price),
     баланс не уходит в минус, кнопка меняет цвет и прыгает на новое место.
  3. Лимит нажатий в сутки на пользователя (в т.ч. неудачных).
"""

from __future__ import annotations

import logging
import random

from aiogram import F, Router
from aiogram.enums import ParseMode
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
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, safe_callback_answer

logger = logging.getLogger(__name__)
router = Router(name="donotbuy")

# Цвета «кнопки» — на каждое нажатие выбирается случайный
_COLORS = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "⚫", "⚪", "🟤"]
_BLANK = "▫️"

_CB_HIT = "donotbuy:hit"
_CB_NOOP = "donotbuy:noop"


def _build_kb(grid_size: int, button_label: str) -> InlineKeyboardMarkup:
    """Сетка из grid_size ячеек; реальная кнопка стоит в случайной из них."""
    color = random.choice(_COLORS)
    hit_index = random.randrange(grid_size)
    cells: list[InlineKeyboardButton] = []
    for i in range(grid_size):
        if i == hit_index:
            cells.append(InlineKeyboardButton(text=f"{color} {button_label}", callback_data=_CB_HIT))
        else:
            cells.append(InlineKeyboardButton(text=_BLANK, callback_data=_CB_NOOP))
    rows = [cells[i:i + 3] for i in range(0, grid_size, 3)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        await reply_and_delete(message, formatter._t["donotbuy_disabled"])
        return

    grid = max(1, cfg.grid_size)
    await message.answer(
        formatter._t["donotbuy_title"].format(
            max_price=cfg.max_price,
            limit=cfg.daily_limit,
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_build_kb(grid, formatter._t["donotbuy_button"]),
    )
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data == _CB_NOOP)
@inject
async def cb_donotbuy_noop(callback: CallbackQuery) -> None:
    await safe_callback_answer(callback)


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

    display = user_link(
        callback.from_user.username, callback.from_user.full_name or "", user_id,
    )

    if charged > 0:
        alert = formatter._t["donotbuy_alert_charged"].format(
            amount=charged, score_word=p.pluralize(charged),
        )
        edit_text = formatter._t["donotbuy_hit"].format(
            user=display, amount=charged, score_word=p.pluralize(charged),
        )
    else:
        alert = formatter._t["donotbuy_alert_zero"]
        edit_text = formatter._t["donotbuy_hit_zero"].format(user=display)

    # Кнопка меняет цвет и прыгает на новое место
    try:
        await callback.message.edit_text(
            edit_text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_build_kb(max(1, cfg.grid_size), formatter._t["donotbuy_button"]),
        )
    except Exception:
        pass

    await safe_callback_answer(callback, alert, show_alert=True)
