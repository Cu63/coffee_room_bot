"""Запуск TTT и BJ из принятого приглашения на дуэль.

Ставки обоих уже списаны с меткой приглашения (duel:<id>), а приглашение забрано
из Redis: дальше игра живёт как обычная, но выплаты идут с той же меткой.
"""

from __future__ import annotations

import random
import time

from aiogram.types import CallbackQuery

from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.stake_service import StakeService
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.blackjack.helpers import (
    _BJ_GAME_TTL,
    _BJ_REDIS_BUFFER,
    _DELETE_DELAY,
    _bj_key,
    _deal,
    _hand_alert,
    _play_kb,
    _settle_game,
    _side_of,
    _turn_text,
)
from bot.presentation.handlers.tictactoe.game_logic import (
    _TTT_GAME_TTL,
    _TTT_REDIS_BUFFER,
    _game_kb,
    _render_board,
    _ttt_key,
)
from bot.presentation.utils import edit_callback_message, safe_callback_answer, schedule_delete


def _players(invite: dict) -> list[tuple[int, str, str]]:
    """Создатель и цель в случайном порядке: первый ходит первым."""
    players = [
        (invite["challenger_id"], invite["challenger_name"], invite["challenger_username"]),
        (invite["target_id"], invite["target_name"], invite["target_username"]),
    ]
    random.shuffle(players)
    return players


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
    (x_id, x_name, x_username), (o_id, o_name, o_username) = _players(invite)
    data = {
        "game_id": game_id,
        "ref": f"duel:{invite['invite_id']}",
        "pot": invite.get("pot", False),
        "state": "playing",
        "player_x": x_id,
        "player_x_name": x_name,
        "player_x_username": x_username,
        "player_o": o_id,
        "player_o_name": o_name,
        "player_o_username": o_username,
        "board": [0] * 9,
        "history_x": [],
        "history_o": [],
        "moves": 0,
        "turn": "x",
        "bet": bet,
        "chat_id": chat_id,
        "message_id": cb.message.message_id,
        "created_at": time.time(),
        "expires_at": time.time() + _TTT_GAME_TTL,
    }
    await store.game_create(_ttt_key(chat_id, game_id), data, _TTT_GAME_TTL + _TTT_REDIS_BUFFER)

    x_display = user_link(x_username or None, x_name, x_id)
    o_display = user_link(o_username or None, o_name, o_id)
    text = (
        f"🎮 <b>Крестики-нолики — дуэль</b>\n\n"
        f"❌ {x_display}  vs  ⭕ {o_display}\n"
        f"Ставка: <b>{bet} {p.pluralize(bet)}</b>\n\n"
        f"{_render_board([0] * 9, [], [], 'x')}\n\n"
        f"Ходит: {x_display} ❌"
    )
    await edit_callback_message(cb, text, _game_kb(game_id, [0] * 9))
    await safe_callback_answer(cb, "⚔️ Игра началась!")


# ── Запуск BJ из дуэли ───────────────────────────────────────────────────


async def _start_bj_from_duel(
    cb: CallbackQuery,
    invite: dict,
    game_id: str,
    chat_id: int,
    bet: int,
    store: RedisStore,
    stakes: StakeService,
    stats_repo: IUserStatsRepository,
    p: ScorePluralizer,
) -> None:
    """Инициализирует BJ игру, в которой оба игрока уже известны."""
    (p1_id, p1_name, p1_username), (p2_id, p2_name, p2_username) = _players(invite)
    data = {
        "game_id": game_id,
        "ref": f"duel:{invite['invite_id']}",
        "pot": invite.get("pot", False),
        "p1_id": p1_id,
        "p1_name": p1_name,
        "p1_username": p1_username,
        "p2_id": p2_id,
        "p2_name": p2_name,
        "p2_username": p2_username,
        "p1_done": False,
        "p2_done": False,
        "p1_busted": False,
        "p2_busted": False,
        "bet": bet,
        "chat_id": chat_id,
        "message_id": cb.message.message_id,
        "created_at": time.time(),
    }
    _deal(data)

    if data["p1_done"] and data["p2_done"]:
        # Натуральный блекджек — сразу вскрытие. Игра никуда не сохранялась, а приглашение
        # уже забрано: рассчитать её больше некому
        text = await _settle_game(data, stakes, stats_repo, p, chat_id)
        await edit_callback_message(cb, text)
        if cb.message.bot:
            schedule_delete(cb.message.bot, cb.message, delay=_DELETE_DELAY)
        await safe_callback_answer(cb, "\U0001f3b0 Блекджек!")
        return

    await store.game_create(_bj_key(chat_id, game_id), data, _BJ_GAME_TTL + _BJ_REDIS_BUFFER)
    await edit_callback_message(cb, _turn_text(data, p), _play_kb(game_id))
    side = _side_of(data, cb.from_user.id)
    await safe_callback_answer(cb, "⚔️ Игра началась!\n\n" + _hand_alert(data, side), show_alert=True)
