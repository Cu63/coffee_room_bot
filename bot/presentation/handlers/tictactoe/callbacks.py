"""Callback-хендлеры: принятие вызова, ходы, noop.

Каждый ход — compare-and-set по прочитанному снимку игры (store.game_save / game_claim):
двойное нажатие или ход по устаревшему полю не запишется и не рассчитает игру дважды.
"""

from __future__ import annotations

import logging
import random
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.daily_leaderboard_repository import IDailyLeaderboardRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.iq_service import IqService
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import User
from bot.domain.ledger import LedgerKind
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import now_msk
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis._games import GameSnapshot
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._iq_utils import award_game_iq
from bot.presentation.handlers.tictactoe.game_logic import (
    _ACCEPT_LOCK_TTL,
    _CELL_O,
    _CELL_X,
    _DELETE_DELAY,
    _MAX_PIECES,
    _TTT_GAME_TTL,
    _TTT_LOBBY_TTL,
    _TTT_REDIS_BUFFER,
    _check_winner,
    _game_kb,
    _game_ref,
    _is_draw,
    _render_board,
    _ttt_key,
)
from bot.presentation.utils import check_gameban, safe_callback_answer, schedule_delete
from bot.presentation.utils import edit_callback_message as _edit

logger = logging.getLogger(__name__)
router = Router(name="ttt_callbacks")

_NOT_FOUND = "Игра завершена или не найдена."


def _display(data: dict, side: str) -> str:
    return user_link(data[f"player_{side}_username"] or None, data[f"player_{side}_name"], data[f"player_{side}"])


# ─── Callback: принять вызов ──────────────────────────────────────────


@router.callback_query(F.data.startswith("ttt:accept:"))
@inject
async def cb_ttt_accept(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    score_repo: FromDishka[IScoreRepository],
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

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, user_id, chat_id, formatter._t)
    if ban_msg:
        await safe_callback_answer(cb, ban_msg, show_alert=True)
        return

    key = _ttt_key(chat_id, game_id)
    snapshot = await store.game_load(key)
    if snapshot is None:
        await safe_callback_answer(cb, formatter._t["ttt_expired"], show_alert=True)
        return
    data = snapshot.data

    if data["state"] != "lobby":
        await safe_callback_answer(cb, "Игра уже началась.", show_alert=True)
        return

    if data["player_x"] == user_id:
        await safe_callback_answer(cb, "Нельзя играть с самим собой.", show_alert=True)
        return

    # Лобби без expires_at создано до появления поля — считаем от created_at
    expires_at = data.get("expires_at") or data.get("created_at", 0) + _TTT_LOBBY_TTL
    if expires_at < time.time():
        await safe_callback_answer(cb, formatter._t["ttt_expired"], show_alert=True)
        return

    # Одно принятие на лобби: второй нажавший (или двойное нажатие) не будет списан
    accept_lock = f"ttt:lock:accept:{chat_id}:{game_id}"
    if not await store.game_lock(accept_lock, _ACCEPT_LOCK_TTL):
        await safe_callback_answer(cb, "Игра уже началась.", show_alert=True)
        return

    await user_repo.upsert(User(id=user_id, username=cb.from_user.username, full_name=cb.from_user.full_name))

    bet = data["bet"]
    p = pluralizer
    ref = _game_ref(data)

    # Списываем ставку с принимающего
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=bet,
        emoji=SPECIAL_EMOJI.get("ttt", "🎮"),
        kind=LedgerKind.STAKE,
        ref=ref,
    )
    if not result.success:
        await store.game_unlock(accept_lock)
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(result.current_balance)
        await safe_callback_answer(
            cb,
            f"Недостаточно баллов. Нужно: {bet} {sw}, у тебя: {result.current_balance} {sw_bal}.",
            show_alert=True,
        )
        return

    # Случайно распределяем роли X и O (X ходит первым)
    data["player_o"] = user_id
    data["player_o_name"] = cb.from_user.full_name or ""
    data["player_o_username"] = cb.from_user.username or ""
    if random.choice([True, False]):
        for field in ("", "_name", "_username"):
            data[f"player_x{field}"], data[f"player_o{field}"] = data[f"player_o{field}"], data[f"player_x{field}"]
    data["state"] = "playing"
    data["turn"] = "x"
    data["moves"] = 0
    data["message_id"] = cb.message.message_id
    data["expires_at"] = time.time() + _TTT_GAME_TTL

    # Лобби → игра, только если лобби ещё ждёт: если его уже забрал цикл истечения
    # (и вернул ставку создателю), игру не воскрешаем, а ставку принимающего возвращаем
    if await store.game_replace_if_state(key, "lobby", data, _TTT_GAME_TTL + _TTT_REDIS_BUFFER) is None:
        await score_repo.add_delta(user_id, chat_id, bet, kind=LedgerKind.REFUND, ref=ref)
        await safe_callback_answer(cb, formatter._t["ttt_expired"], show_alert=True)
        return

    text = formatter._t["ttt_started"].format(
        player_x=_display(data, "x"),
        player_o=_display(data, "o"),
        bet=bet,
        score_word=p.pluralize(bet),
        board=_render_board(data["board"], data["history_x"], data["history_o"], "x"),
        turn=_display(data, "x"),
        turn_symbol=_CELL_X,
    )
    await _edit(cb, text, _game_kb(game_id, data["board"]))
    await safe_callback_answer(cb, "⚔️ Игра началась!")


# ─── Callback: ход ────────────────────────────────────────────────────


async def _answer_stale(cb: CallbackQuery, store: RedisStore, snapshot: GameSnapshot) -> None:
    """Ход посчитан по устаревшему снимку: двойное нажатие или игру уже закончили."""
    if await store.game_load(snapshot.key) is None:
        await safe_callback_answer(cb, _NOT_FOUND, show_alert=True)
    else:
        await safe_callback_answer(cb)  # второе нажатие — ход уже сделан


@router.callback_query(F.data.startswith("ttt:move:"))
@inject
async def cb_ttt_move(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_repo: FromDishka[IScoreRepository],
    stats_repo: FromDishka[IUserStatsRepository],
    lb_repo: FromDishka[IDailyLeaderboardRepository],
    iq_service: FromDishka[IqService],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
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

    snapshot = await store.game_load(_ttt_key(chat_id, game_id))
    if snapshot is None:
        await safe_callback_answer(cb, _NOT_FOUND, show_alert=True)
        return
    data = snapshot.data

    if data["state"] != "playing":
        await safe_callback_answer(cb, "Игра не активна.", show_alert=True)
        return

    turn = data["turn"]
    if user_id != data[f"player_{turn}"]:
        if user_id in (data["player_x"], data["player_o"]):
            await safe_callback_answer(cb, "Сейчас не твой ход!")
        else:
            await safe_callback_answer(cb, "Ты не участник этой игры.", show_alert=True)
        return

    board = data["board"]
    if board[cell_idx] != 0:
        await safe_callback_answer(cb, "Клетка занята!")
        return

    # Ставим фигуру; у игрока уже _MAX_PIECES — самая старая исчезает
    history = data[f"history_{turn}"]
    if len(history) >= _MAX_PIECES:
        board[history.pop(0)] = 0
    board[cell_idx] = 1 if turn == "x" else 2
    history.append(cell_idx)
    # Игры, начатые до счётчика ходов, досчитываем по фигурам на поле
    data["moves"] = data.get("moves", len(data["history_x"]) + len(data["history_o"]) - 1) + 1

    winner = _check_winner(board)
    draw = not winner and _is_draw(data["moves"])
    p = pluralizer
    bet = data["bet"]

    if winner or draw:
        # Забираем игру: рассчитает её ровно один запрос
        if not await store.game_claim(snapshot):
            await _answer_stale(cb, store, snapshot)
            return

        ref = _game_ref(data)
        board_text = _render_board(board, data["history_x"], data["history_o"], turn)
        if draw:
            for side in ("x", "o"):
                await score_repo.add_delta(data[f"player_{side}"], chat_id, bet, kind=LedgerKind.REFUND, ref=ref)
            text = formatter._t["ttt_draw"].format(
                player_x=_display(data, "x"),
                player_o=_display(data, "o"),
                board=board_text,
                bet=bet,
                score_word=p.pluralize(bet),
            )
        else:
            winner_side = "x" if winner == 1 else "o"
            winner_id = data[f"player_{winner_side}"]
            total_pot = bet * 2
            await score_repo.add_delta(winner_id, chat_id, total_pot, kind=LedgerKind.PRIZE, ref=ref)
            await stats_repo.add_win(winner_id, chat_id, "ttt")
            await lb_repo.add_game_win(winner_id, chat_id, "ttt", now_msk().date())
            # +IQ за победу (с учётом дневного лимита)
            await award_game_iq(store, iq_service, config.iq, winner_id, chat_id)
            text = formatter._t["ttt_win"].format(
                player_x=_display(data, "x"),
                player_o=_display(data, "o"),
                board=board_text,
                winner=_display(data, winner_side),
                winner_symbol=_CELL_X if winner == 1 else _CELL_O,
                prize=total_pot,
                score_word=p.pluralize(total_pot),
            )

        await _edit(cb, text, None)
        if cb.message.bot:
            schedule_delete(cb.message.bot, cb.message, delay=_DELETE_DELAY)
        await safe_callback_answer(cb)
        return

    # Переключаем ход; каждый ход продлевает срок игры
    next_turn = "o" if turn == "x" else "x"
    data["turn"] = next_turn
    data["expires_at"] = time.time() + _TTT_GAME_TTL
    if await store.game_save(snapshot, data, _TTT_GAME_TTL + _TTT_REDIS_BUFFER) is None:
        await _answer_stale(cb, store, snapshot)
        return

    text = formatter._t["ttt_turn"].format(
        player_x=_display(data, "x"),
        player_o=_display(data, "o"),
        bet=bet,
        score_word=p.pluralize(bet),
        board=_render_board(board, data["history_x"], data["history_o"], next_turn),
        turn=_display(data, next_turn),
        turn_symbol=_CELL_X if next_turn == "x" else _CELL_O,
    )
    await _edit(cb, text, _game_kb(game_id, board))
    await safe_callback_answer(cb)


# ─── Callback: noop (занятая клетка) ─────────────────────────────────


@router.callback_query(F.data.startswith("ttt:noop:"))
async def cb_ttt_noop(cb: CallbackQuery) -> None:
    await safe_callback_answer(cb)
