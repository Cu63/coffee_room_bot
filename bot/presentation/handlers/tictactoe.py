"""Хендлер /ttt — дуэльные исчезающие крестики-нолики.

Правила:
  1. /ttt <ставка> — создать игру, ставка списывается сразу
  2. Второй игрок принимает (ставка тоже списывается)
  3. Первый ход определяется случайно
  4. Каждый игрок может иметь максимум 3 фигуры на поле
  5. При постановке 4-й — самая старая исчезает
  6. Победа — 3 в ряд (стандартные комбинации)
"""

from __future__ import annotations

import json
import logging
import random
import time

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.interfaces.daily_leaderboard_repository import IDailyLeaderboardRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import now_msk
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, safe_callback_answer, schedule_delete

logger = logging.getLogger(__name__)
router = Router(name="tictactoe")

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


# ─── /ttt ──────────────────────────────────────────────────────────────


@router.message(Command("ttt"))
@inject
async def cmd_ttt(
    message: Message,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None or message.bot is None:
        return

    args = (message.text or "").split()[1:]
    cfg = config.tictactoe
    p = pluralizer

    if not args:
        sw_max = p.pluralize(cfg.max_bet)
        await reply_and_delete(
            message,
            formatter._t["ttt_usage"].format(
                min_bet=cfg.min_bet,
                max_bet=cfg.max_bet,
                score_word=sw_max,
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # Парсим ставку
    try:
        bet = int(args[0])
        if bet <= 0:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, "❌ Ставка должна быть положительным числом.")
        return

    if bet < cfg.min_bet:
        sw = p.pluralize(cfg.min_bet)
        await reply_and_delete(message, f"❌ Минимальная ставка: {cfg.min_bet} {sw}.")
        return

    if bet > cfg.max_bet:
        sw = p.pluralize(cfg.max_bet)
        await reply_and_delete(message, f"❌ Максимальная ставка: {cfg.max_bet} {sw}.")
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    # Upsert пользователя
    await user_repo.upsert(
        User(
            id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    )

    # Проверяем баланс
    score = await score_service.get_score(user_id, chat_id)
    if score.value < bet:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(score.value)
        await reply_and_delete(
            message,
            formatter._t["ttt_not_enough"].format(
                cost=bet, score_word=sw, balance=score.value, score_word_balance=sw_bal,
            ),
        )
        return

    # Списываем ставку с создателя
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=bet,
        emoji=SPECIAL_EMOJI.get("ttt", "🎮"),
    )
    if not result.success:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(result.current_balance)
        await reply_and_delete(
            message,
            formatter._t["ttt_not_enough"].format(
                cost=bet, score_word=sw, balance=result.current_balance, score_word_balance=sw_bal,
            ),
        )
        return

    # Создаём лобби
    game_id = _make_game_id()
    display = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
    sw_bet = p.pluralize(bet)

    data = {
        "game_id": game_id,
        "state": "lobby",  # lobby → playing → finished
        "player_x": user_id,
        "player_x_name": message.from_user.full_name or "",
        "player_x_username": message.from_user.username or "",
        "player_o": None,
        "player_o_name": "",
        "player_o_username": "",
        "board": [0] * 9,
        "history_x": [],
        "history_o": [],
        "turn": "",  # определится при accept
        "bet": bet,
        "chat_id": chat_id,
        "message_id": 0,
        "created_at": time.time(),
    }

    key = _ttt_key(chat_id, game_id)
    await store._r.set(key, json.dumps(data), ex=_TTT_LOBBY_TTL)

    lobby_text = formatter._t["ttt_lobby"].format(
        user=display, bet=bet, score_word=sw_bet,
    )
    sent = await message.answer(
        lobby_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_lobby_kb(game_id),
    )

    # Сохраняем message_id
    data["message_id"] = sent.message_id
    await store._r.set(key, json.dumps(data), ex=_TTT_LOBBY_TTL)


# ─── Callback: принять вызов ──────────────────────────────────────────


@router.callback_query(F.data.startswith("ttt:accept:"))
@inject
async def cb_ttt_accept(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    pluralizer: FromDishka[ScorePluralizer],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    parts = cb.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(cb)
        return

    game_id = parts[2]
    chat_id = cb.message.chat.id
    user_id = cb.from_user.id
    key = _ttt_key(chat_id, game_id)

    raw = await store._r.get(key)
    if raw is None:
        await safe_callback_answer(cb, formatter._t["ttt_expired"], show_alert=True)
        return

    data = json.loads(raw)

    if data["state"] != "lobby":
        await safe_callback_answer(cb, "Игра уже началась.", show_alert=True)
        return

    if data["player_x"] == user_id:
        await safe_callback_answer(cb, "Нельзя играть с самим собой.", show_alert=True)
        return

    # Upsert пользователя
    await user_repo.upsert(
        User(
            id=user_id,
            username=cb.from_user.username,
            full_name=cb.from_user.full_name,
        )
    )

    bet = data["bet"]
    p = pluralizer

    # Списываем ставку с принимающего
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=bet,
        emoji=SPECIAL_EMOJI.get("ttt", "🎮"),
    )
    if not result.success:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(result.current_balance)
        await safe_callback_answer(
            cb,
            f"Недостаточно баллов. Нужно: {bet} {sw}, у тебя: {result.current_balance} {sw_bal}.",
            show_alert=True,
        )
        return

    # Случайно распределяем роли X и O (X ходит первым)
    data["state"] = "playing"
    if random.choice([True, False]):
        # Создатель остаётся X, принимающий — O
        data["player_o"] = user_id
        data["player_o_name"] = cb.from_user.full_name or ""
        data["player_o_username"] = cb.from_user.username or ""
    else:
        # Меняем местами: принимающий становится X, создатель — O
        data["player_o"] = data["player_x"]
        data["player_o_name"] = data["player_x_name"]
        data["player_o_username"] = data["player_x_username"]
        data["player_x"] = user_id
        data["player_x_name"] = cb.from_user.full_name or ""
        data["player_x_username"] = cb.from_user.username or ""
    data["turn"] = "x"  # X всегда ходит первым

    await store._r.set(key, json.dumps(data), ex=_TTT_GAME_TTL)

    # Определяем имена
    x_display = user_link(
        data["player_x_username"] or None, data["player_x_name"], data["player_x"],
    )
    o_display = user_link(
        data["player_o_username"] or None, data["player_o_name"], data["player_o"],
    )

    turn_player = x_display
    turn_symbol = _CELL_X

    board_text = _render_board(data["board"], data["history_x"], data["history_o"], "x")
    sw_bet = p.pluralize(bet)

    text = formatter._t["ttt_started"].format(
        player_x=x_display,
        player_o=o_display,
        bet=bet,
        score_word=sw_bet,
        board=board_text,
        turn=turn_player,
        turn_symbol=turn_symbol,
    )

    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_game_kb(game_id, data["board"]),
        )
    except Exception:
        pass

    await safe_callback_answer(cb, "⚔️ Игра началась!")


# ─── Callback: ход ────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("ttt:move:"))
@inject
async def cb_ttt_move(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    score_repo: FromDishka[IScoreRepository],
    stats_repo: FromDishka[IUserStatsRepository],
    lb_repo: FromDishka[IDailyLeaderboardRepository],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    parts = cb.data.split(":")
    if len(parts) < 4:
        await safe_callback_answer(cb)
        return

    game_id = parts[2]
    try:
        cell_idx = int(parts[3])
    except ValueError:
        await safe_callback_answer(cb)
        return

    if cell_idx < 0 or cell_idx > 8:
        await safe_callback_answer(cb)
        return

    chat_id = cb.message.chat.id
    user_id = cb.from_user.id
    key = _ttt_key(chat_id, game_id)

    raw = await store._r.get(key)
    if raw is None:
        await safe_callback_answer(cb, "Игра завершена или не найдена.", show_alert=True)
        return

    data = json.loads(raw)

    if data["state"] != "playing":
        await safe_callback_answer(cb, "Игра не активна.", show_alert=True)
        return

    # Определяем, кто ходит
    turn = data["turn"]
    if turn == "x" and user_id != data["player_x"]:
        if user_id == data["player_o"]:
            await safe_callback_answer(cb, "Сейчас не твой ход!", show_alert=False)
        else:
            await safe_callback_answer(cb, "Ты не участник этой игры.", show_alert=True)
        return
    if turn == "o" and user_id != data["player_o"]:
        if user_id == data["player_x"]:
            await safe_callback_answer(cb, "Сейчас не твой ход!", show_alert=False)
        else:
            await safe_callback_answer(cb, "Ты не участник этой игры.", show_alert=True)
        return

    board = data["board"]
    history_x = data["history_x"]
    history_o = data["history_o"]

    # Проверяем, что клетка свободна
    if board[cell_idx] != 0:
        await safe_callback_answer(cb, "Клетка занята!", show_alert=False)
        return

    # Ставим фигуру
    piece = 1 if turn == "x" else 2
    history = history_x if turn == "x" else history_o

    # Если у игрока уже MAX_PIECES, убираем самую старую
    if len(history) >= _MAX_PIECES:
        oldest = history.pop(0)
        board[oldest] = 0

    board[cell_idx] = piece
    history.append(cell_idx)

    data["board"] = board
    data["history_x"] = history_x
    data["history_o"] = history_o

    # Проверяем победу
    winner = _check_winner(board)
    draw = _is_draw(board, history_x, history_o) if winner == 0 else False

    p = pluralizer
    x_display = user_link(
        data["player_x_username"] or None, data["player_x_name"], data["player_x"],
    )
    o_display = user_link(
        data["player_o_username"] or None, data["player_o_name"], data["player_o"],
    )
    bet = data["bet"]
    sw_bet = p.pluralize(bet)

    if winner or draw:
        # Игра окончена
        data["state"] = "finished"
        await store._r.delete(key)

        total_pot = bet * 2
        board_text = _render_board(board, history_x, history_o, turn)

        if draw:
            # Возврат ставок
            await score_repo.add_delta(data["player_x"], chat_id, bet)
            await score_repo.add_delta(data["player_o"], chat_id, bet)

            text = formatter._t["ttt_draw"].format(
                player_x=x_display,
                player_o=o_display,
                board=board_text,
                bet=bet,
                score_word=sw_bet,
            )
        else:
            winner_id = data["player_x"] if winner == 1 else data["player_o"]
            loser_id = data["player_o"] if winner == 1 else data["player_x"]
            winner_display = x_display if winner == 1 else o_display
            winner_symbol = _CELL_X if winner == 1 else _CELL_O

            # Выплата победителю
            await score_repo.add_delta(winner_id, chat_id, total_pot)

            # Записываем победу
            await stats_repo.add_win(winner_id, chat_id, "ttt")
            await lb_repo.add_game_win(winner_id, chat_id, "ttt", now_msk().date())

            sw_pot = p.pluralize(total_pot)
            text = formatter._t["ttt_win"].format(
                player_x=x_display,
                player_o=o_display,
                board=board_text,
                winner=winner_display,
                winner_symbol=winner_symbol,
                prize=total_pot,
                score_word=sw_pot,
            )

        try:
            await cb.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
                reply_markup=None,
            )
            if cb.message.bot:
                schedule_delete(cb.message.bot, cb.message, delay=_DELETE_DELAY)
        except Exception:
            pass

        await safe_callback_answer(cb)
        return

    # Переключаем ход
    next_turn = "o" if turn == "x" else "x"
    data["turn"] = next_turn
    await store._r.set(key, json.dumps(data), ex=_TTT_GAME_TTL)

    turn_player = x_display if next_turn == "x" else o_display
    turn_symbol = _CELL_X if next_turn == "x" else _CELL_O

    board_text = _render_board(board, history_x, history_o, next_turn)
    text = formatter._t["ttt_turn"].format(
        player_x=x_display,
        player_o=o_display,
        bet=bet,
        score_word=sw_bet,
        board=board_text,
        turn=turn_player,
        turn_symbol=turn_symbol,
    )

    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_game_kb(game_id, board),
        )
    except Exception:
        pass

    await safe_callback_answer(cb)


# ─── Callback: noop (занятая клетка) ─────────────────────────────────


@router.callback_query(F.data.startswith("ttt:noop:"))
async def cb_ttt_noop(cb: CallbackQuery) -> None:
    await safe_callback_answer(cb)
