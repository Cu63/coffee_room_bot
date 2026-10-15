"""Shared constants and utility functions for duel handlers."""

from __future__ import annotations

import random
import time

from aiogram.enums import ButtonStyle
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

# ── Константы ───────────────────────────────────────────────────────────
_DUEL_PREFIX = "duel:invite:"
_DUEL_TIMEOUT = 120      # секунд — реальный таймаут для игрока
# Redis-TTL с часовым запасом поверх expires_at: duel_cleanup_loop должен успеть
# вернуть ставку, даже если бот был недоступен (деплой), до того как Redis удалит ключ
_DUEL_INVITE_TTL = _DUEL_TIMEOUT + 3600

_SUPPORTED_GAMES = ("ttt", "bj")


# ── Ключ приглашения ────────────────────────────────────────────────────

def _duel_key(chat_id: int, invite_id: str) -> str:
    return f"{_DUEL_PREFIX}{chat_id}:{invite_id}"


def _make_invite_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


# ── Клавиатура приглашения ───────────────────────────────────────────────

def _invite_kb(invite_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u2705 Принять",
                    callback_data=f"duel:accept:{invite_id}",
                    style=ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    text="\u274c Отказаться",
                    callback_data=f"duel:decline:{invite_id}",
                    style=ButtonStyle.DANGER,
                ),
            ]
        ]
    )
