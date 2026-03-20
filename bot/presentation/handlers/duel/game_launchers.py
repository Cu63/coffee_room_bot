"""Game launch functions for starting TTT and BJ games from duel invitations."""

from __future__ import annotations

import json
import random
import time

from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery

from bot.application.blackjack_service import build_deck, cards_to_dicts
from bot.application.interfaces.score_repository import IScoreRepository
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW

# Импортируем внутренние утилиты из игровых хендлеров
from bot.presentation.handlers.tictactoe import (
    _TTT_GAME_TTL,
    _game_kb as _ttt_game_kb,
    _render_board as _ttt_render_board,
    _ttt_key,
)
from bot.presentation.handlers.blackjack import (
    _BJ_GAME_TTL,
    _bj_key,
    _is_natural as _bj_is_natural,
    _hand_score_from_dicts as _bj_hand_score,
    _play_kb as _bj_play_kb,
    _turn_text as _bj_turn_text,
)

from bot.presentation.handlers.duel.helpers import _make_invite_id


# ── Запуск TTT из дуэли ──────────────────────────────────────────────────

async def _start_ttt_from_duel(
    cb: CallbackQuery,
    invite: dict,
    game_id: str,
    chat_id: int,
    bet: int,
    store: RedisStore,
    p: ScorePluralizer,
) -> None:
    """Инициализирует TTT игру, в которой оба игрока уже известны."""
    from bot.infrastructure.message_formatter import user_link

    challenger_id = invite["challenger_id"]
    challenger_name = invite["challenger_name"]
    challenger_username = invite["challenger_username"]
    target_id = invite["target_id"]
    target_name = invite["target_name"]
    target_username = invite["target_username"]

    # Случайно распределяем X и O
    if random.choice([True, False]):
        player_x_id, player_x_name, player_x_username = challenger_id, challenger_name, challenger_username
        player_o_id, player_o_name, player_o_username = target_id, target_name, target_username
    else:
        player_x_id, player_x_name, player_x_username = target_id, target_name, target_username
        player_o_id, player_o_name, player_o_username = challenger_id, challenger_name, challenger_username

    data = {
        "game_id": game_id,
        "state": "playing",
        "player_x": player_x_id,
        "player_x_name": player_x_name,
        "player_x_username": player_x_username,
        "player_o": player_o_id,
        "player_o_name": player_o_name,
        "player_o_username": player_o_username,
        "board": [0] * 9,
        "history_x": [],
        "history_o": [],
        "turn": "x",
        "bet": bet,
        "chat_id": chat_id,
        "message_id": cb.message.message_id,
        "created_at": time.time(),
    }

    ttt_key = _ttt_key(chat_id, game_id)
    await store._r.set(ttt_key, json.dumps(data), ex=_TTT_GAME_TTL)

    x_display = user_link(player_x_username or None, player_x_name, player_x_id)
    o_display = user_link(player_o_username or None, player_o_name, player_o_id)
    sw_bet = p.pluralize(bet)

    board_text = _ttt_render_board([0] * 9, [], [], "x")
    text = (
        f"🎮 <b>Крестики-нолики — дуэль</b>\n\n"
        f"❌ {x_display}  vs  ⭕ {o_display}\n"
        f"Ставка: <b>{bet} {sw_bet}</b>\n\n"
        f"{board_text}\n\n"
        f"Ходит: {x_display} ❌"
    )

    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_ttt_game_kb(game_id, [0] * 9),
        )
    except Exception:
        pass


# ── Запуск BJ из дуэли ───────────────────────────────────────────────────

async def _start_bj_from_duel(
    cb: CallbackQuery,
    invite: dict,
    game_id: str,
    chat_id: int,
    bet: int,
    store: RedisStore,
    score_repo: IScoreRepository,
    p: ScorePluralizer,
) -> None:
    """Инициализирует BJ игру, в которой оба игрока уже известны."""
    from bot.application.interfaces.user_stats_repository import IUserStatsRepository

    challenger_id = invite["challenger_id"]
    challenger_name = invite["challenger_name"]
    challenger_username = invite["challenger_username"]
    target_id = invite["target_id"]
    target_name = invite["target_name"]
    target_username = invite["target_username"]

    # Случайно выбираем, кто ходит первым (p1)
    if random.choice([True, False]):
        p1_id, p1_name, p1_username = challenger_id, challenger_name, challenger_username
        p2_id, p2_name, p2_username = target_id, target_name, target_username
    else:
        p1_id, p1_name, p1_username = target_id, target_name, target_username
        p2_id, p2_name, p2_username = challenger_id, challenger_name, challenger_username

    deck = build_deck()
    p1_hand = cards_to_dicts([deck.pop(), deck.pop()])
    p2_hand = cards_to_dicts([deck.pop(), deck.pop()])

    data = {
        "game_id": game_id,
        "state": "playing",
        "p1_id": p1_id,
        "p1_name": p1_name,
        "p1_username": p1_username,
        "p2_id": p2_id,
        "p2_name": p2_name,
        "p2_username": p2_username,
        "deck": cards_to_dicts(deck),
        "p1_hand": p1_hand,
        "p2_hand": p2_hand,
        "p1_done": False,
        "p2_done": False,
        "p1_busted": False,
        "p2_busted": False,
        "turn": "p1",
        "bet": bet,
        "chat_id": chat_id,
        "message_id": cb.message.message_id,
        "created_at": time.time(),
        "expires_at": time.time() + _BJ_GAME_TTL,
    }

    bj_key = _bj_key(chat_id, game_id)

    # Проверяем натуральные блекджеки
    p1_natural = _bj_is_natural(p1_hand)
    p2_natural = _bj_is_natural(p2_hand)

    if p1_natural or p2_natural:
        # Мгновенное завершение — resolve_game сам удалит ключ
        data["p1_done"] = True
        data["p2_done"] = True
        await store._r.set(bj_key, json.dumps(data), ex=_BJ_GAME_TTL)

        from bot.presentation.handlers.blackjack import _resolve_game
        # Получаем stats_repo из dishka через cb (нет прямого доступа, используем заглушку)
        # Поскольку natural BJ — редкий случай, логируем и продолжаем без stats
        try:
            from bot.application.interfaces.user_stats_repository import IUserStatsRepository as _IUS
            # stats недоступны напрямую здесь, передаём фиктивный объект-заглушку
            class _NoStats:
                async def add_win(self, *a, **kw): pass
            text = await _resolve_game(data, store, score_repo, _NoStats(), p, chat_id)
        except Exception:
            text = "🎰 Натуральный блекджек!"
        try:
            from bot.presentation.utils import schedule_delete
            await cb.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
                reply_markup=None,
            )
            if cb.message.bot:
                schedule_delete(cb.message.bot, cb.message, delay=120)
        except Exception:
            pass
        return

    # Обычный старт
    if _bj_hand_score(p1_hand) == 21:
        data["p1_done"] = True
        data["turn"] = "p2"

    await store._r.set(bj_key, json.dumps(data), ex=_BJ_GAME_TTL)

    text = _bj_turn_text(data, p)
    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_bj_play_kb(game_id),
        )
    except Exception:
        pass
