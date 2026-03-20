"""Callback-хендлеры: принятие вызова, ходы, noop."""

from __future__ import annotations

import json
import logging
import random

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.daily_leaderboard_repository import IDailyLeaderboardRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import now_msk
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.tictactoe.game_logic import (
    _CELL_O,
    _CELL_X,
    _DELETE_DELAY,
    _MAX_PIECES,
    _TTT_GAME_TTL,
    _TTT_LOBBY_TTL,
    _check_winner,
    _game_kb,
    _is_draw,
    _render_board,
    _ttt_key,
)
from bot.presentation.utils import NO_PREVIEW, check_gameban, safe_callback_answer, schedule_delete

logger = logging.getLogger(__name__)
router = Router(name="ttt_callbacks")


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

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, user_id, chat_id, formatter._t)
    if ban_msg:
        await safe_callback_answer(cb, ban_msg, show_alert=True)
        return

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

    # ── Баг 1: TOCTOU (двойное нажатие / ретрай сети) ─────────────────
    # Берём per-game mutex: SET NX EX 5.
    # Только один coroutine за раз проходит через критическую секцию
    # GET → mutate → SET/DELETE. Остальные получают "не твой ход" и
    # возвращаются без каких-либо изменений состояния.
    lock_key = f"ttt:lock:{chat_id}:{game_id}"
    acquired = await store._r.set(lock_key, "1", nx=True, ex=5)
    if not acquired:
        # Другой запрос уже обрабатывает этот ход — тихо игнорируем
        await safe_callback_answer(cb)
        return

    try:
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
            # Игра окончена.
            # ── Баг 2: двойная выплата ────────────────────────────────
            # Атомарно удаляем ключ игры. Если delete вернул 0 — другой
            # запрос уже завершил игру и сделал выплату, выходим.
            data["state"] = "finished"
            deleted = await store._r.delete(key)
            if not deleted:
                logger.warning("ttt: game %s already finished by concurrent request", game_id)
                await safe_callback_answer(cb)
                return

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

    finally:
        # Всегда освобождаем lock — даже при исключении
        await store._r.delete(lock_key)


# ─── Callback: noop (занятая клетка) ─────────────────────────────────


@router.callback_query(F.data.startswith("ttt:noop:"))
async def cb_ttt_noop(cb: CallbackQuery) -> None:
    await safe_callback_answer(cb)
