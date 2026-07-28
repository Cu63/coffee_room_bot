"""Пакет хендлеров дуэльного блекджека.

Правила:
  1. /bj <ставка> — создать лобби, ставка списывается сразу
  2. Второй игрок принимает (ставка тоже списывается)
  3. Обоим раздаётся по 2 карты из общей колоды
  4. Случайно выбирается, кто ходит первым
  5. Каждый игрок по очереди: «Ещё» (взять карту) или «Хватит» (остановиться)
  6. Руки соперника скрыты — каждый видит только свои карты
  7. При переборе (>21) ход тихо переходит — соперник не знает о переборе
  8. При 21 очках — автоматически «Хватит»
  9. После того как оба закончили — вскрытие и сравнение очков
  10. Победитель забирает весь банк (2× ставки)
  11. Ничья — ставки возвращаются
"""

from aiogram import Router

from bot.presentation.handlers.blackjack.callbacks import router as callbacks_router
from bot.presentation.handlers.blackjack.helpers import (
    _BJ_GAME_TTL,
    _BJ_LOBBY_TTL,
    _BJ_REDIS_BUFFER,
    _bj_key,
    _hand_score_from_dicts,
    _is_natural,
    _play_kb,
    _resolve_game,
    _turn_text,
)
from bot.presentation.handlers.blackjack.start import router as start_router

router = Router(name="blackjack")
router.include_router(start_router)
router.include_router(callbacks_router)

__all__ = [
    "router",
    "_BJ_GAME_TTL",
    "_BJ_LOBBY_TTL",
    "_BJ_REDIS_BUFFER",
    "_bj_key",
    "_hand_score_from_dicts",
    "_is_natural",
    "_play_kb",
    "_resolve_game",
    "_turn_text",
]
