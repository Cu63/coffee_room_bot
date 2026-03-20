"""Логика игры: константы, проверка победы, рендер поля, клавиатуры."""

from __future__ import annotations

import random
import time

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

# ── Константы ──────────────────────────────────────────────────────────
_TTT_PREFIX = "ttt:"
_TTT_GAME_TTL = 600  # 10 минут на игру целиком
_TTT_LOBBY_TTL = 300  # 5 минут на принятие вызова
_MAX_PIECES = 3  # максимум фигур на поле у каждого игрока
_DELETE_DELAY = 120  # задержка удаления результатов

# Символы для поля
_CELL_EMPTY = "⬜"
_CELL_X = "❌"
_CELL_O = "⭕"
_CELL_X_FADE = "🟥"  # фигура X, которая исчезнет при следующем ходе
_CELL_O_FADE = "🟧"  # фигура O, которая исчезнет при следующем ходе

# Выигрышные комбинации
_WIN_COMBOS = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # ряды
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # колонки
    (0, 4, 8), (2, 4, 6),              # диагонали
]


def _ttt_key(chat_id: int, game_id: str) -> str:
    return f"{_TTT_PREFIX}{chat_id}:{game_id}"


def _check_winner(board: list[int]) -> int:
    """Возвращает 1 (X wins), 2 (O wins) или 0 (нет победителя)."""
    for a, b, c in _WIN_COMBOS:
        if board[a] == board[b] == board[c] != 0:
            return board[a]
    return 0


def _is_draw(board: list[int], history_x: list[int], history_o: list[int]) -> bool:
    """Ничья невозможна в исчезающих крестиках-ноликах — игра идёт бесконечно.

    Однако добавим лимит ходов: если суммарно сделано 50 ходов — ничья.
    """
    return (len(history_x) + len(history_o)) >= 50


def _render_board(
    board: list[int],
    history_x: list[int],
    history_o: list[int],
    turn: str,
) -> str:
    """Рендерит текстовое представление поля."""
    cells = []
    # Определяем, какая фигура исчезнет при следующем ходе текущего игрока
    fade_cell = -1
    if turn == "x" and len(history_x) >= _MAX_PIECES:
        fade_cell = history_x[0]  # самая старая фигура X
    elif turn == "o" and len(history_o) >= _MAX_PIECES:
        fade_cell = history_o[0]  # самая старая фигура O

    for i, val in enumerate(board):
        if val == 0:
            cells.append(_CELL_EMPTY)
        elif val == 1:
            cells.append(_CELL_X_FADE if i == fade_cell else _CELL_X)
        elif val == 2:
            cells.append(_CELL_O_FADE if i == fade_cell else _CELL_O)
    return (
        f"{cells[0]}{cells[1]}{cells[2]}\n"
        f"{cells[3]}{cells[4]}{cells[5]}\n"
        f"{cells[6]}{cells[7]}{cells[8]}"
    )


def _game_kb(game_id: str, board: list[int], active: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура 3×3 для игры."""
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            idx = r * 3 + c
            val = board[idx]
            if val == 0:
                symbol = "·"
            elif val == 1:
                symbol = "✕"
            else:
                symbol = "○"
            cb_data = f"ttt:move:{game_id}:{idx}" if active and val == 0 else f"ttt:noop:{game_id}"
            row.append(InlineKeyboardButton(text=symbol, callback_data=cb_data))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _lobby_kb(game_id: str) -> InlineKeyboardMarkup:
    """Кнопка «Принять вызов»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Принять вызов",
                    callback_data=f"ttt:accept:{game_id}",
                )
            ]
        ]
    )


def _make_game_id() -> str:
    """Генерирует уникальный ID игры."""
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"
