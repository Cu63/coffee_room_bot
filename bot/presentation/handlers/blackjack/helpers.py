"""Вспомогательные функции, константы, клавиатуры и рендеринг для блекджека."""

from __future__ import annotations

import json
import logging
import random
import time

from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from bot.application.blackjack_service import (
    build_deck,
    cards_to_dicts,
    dicts_to_cards,
    format_hand,
    hand_score,
)
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, schedule_delete

logger = logging.getLogger(__name__)

# ── Константы ──────────────────────────────────────────────────────────
_BJ_PREFIX = "bj:duel:"
_BJ_LOBBY_TTL = 300  # 5 минут на принятие вызова
_BJ_GAME_TTL = 600  # 10 минут на игру целиком
_DELETE_DELAY = 120  # задержка удаления результата


# ── Утилиты ────────────────────────────────────────────────────────────


def _bj_key(chat_id: int, game_id: str) -> str:
    return f"{_BJ_PREFIX}{chat_id}:{game_id}"


def _make_game_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


def _hand_score_from_dicts(hand: list[dict]) -> int:
    return hand_score(dicts_to_cards(hand))


def _format_hand_from_dicts(hand: list[dict]) -> str:
    return format_hand(dicts_to_cards(hand))


def _is_natural(hand: list[dict]) -> bool:
    return len(hand) == 2 and _hand_score_from_dicts(hand) == 21


# ── Клавиатуры ─────────────────────────────────────────────────────────


def _lobby_kb(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u2694\ufe0f Принять вызов",
                    callback_data=f"bj:accept:{game_id}",
                )
            ]
        ]
    )


def _play_kb(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="\U0001f0cf Ещё", callback_data=f"bj:hit:{game_id}"),
                InlineKeyboardButton(
                    text="\U0001f6d1 Хватит", callback_data=f"bj:stand:{game_id}"
                ),
            ]
        ]
    )


# ── Рендеринг ─────────────────────────────────────────────────────────


def _player_display(data: dict, prefix: str) -> str:
    return user_link(
        data[f"{prefix}_username"] or None,
        data[f"{prefix}_name"],
        data[f"{prefix}_id"],
    )


def _render_hand_line(
    name: str, hand: list[dict], *, done: bool = False, busted: bool = False
) -> str:
    score = _hand_score_from_dicts(hand)
    cards = _format_hand_from_dicts(hand)
    suffix = ""
    if busted:
        suffix = " \U0001f4a5"
    elif done:
        suffix = " \u270b"
    return f"\U0001f3b4 <b>{name}</b> [{score}{suffix}]:  {cards}"


def _render_table(
    data: dict, *, result_line: str = ""
) -> str:
    p1_display = _player_display(data, "p1")
    p2_display = _player_display(data, "p2")

    lines = [
        _render_hand_line(
            p1_display,
            data["p1_hand"],
            done=data.get("p1_done", False),
            busted=data.get("p1_busted", False),
        ),
        _render_hand_line(
            p2_display,
            data["p2_hand"],
            done=data.get("p2_done", False),
            busted=data.get("p2_busted", False),
        ),
    ]
    if result_line:
        lines.append("")
        lines.append(result_line)
    return "\n".join(lines)


def _turn_text(data: dict, p: ScorePluralizer) -> str:
    """Полный текст сообщения во время хода."""
    bet = data["bet"]
    sw = p.pluralize(bet)
    turn = data["turn"]
    turn_display = _player_display(data, turn)

    table = _render_table(data)
    return (
        f"\U0001f0cf <b>Блекджек — дуэль</b>\n"
        f"Ставка: <b>{bet} {sw}</b>\n\n"
        f"{table}\n\n"
        f"Ходит: {turn_display}"
    )


# ── Логика завершения ──────────────────────────────────────────────────


async def _resolve_game(
    data: dict,
    store: RedisStore,
    score_repo: IScoreRepository,
    stats_repo: IUserStatsRepository,
    p: ScorePluralizer,
    chat_id: int,
) -> str:
    """Определить победителя и выплатить. Возвращает текст результата."""
    game_id = data["game_id"]
    key = _bj_key(chat_id, game_id)

    # Атомарно забираем ключ, чтобы cleanup loop не смог его обработать параллельно
    # Если ключ уже удалён (cleanup loop опередил) — значит рефунд уже выполнен
    if not await store._r.delete(key):
        logger.warning("BJ game %s already cleaned up by loop, skipping resolve", game_id)
        return "\u23f0 Игра была завершена по таймауту. Ставки возвращены."

    bet = data["bet"]
    total_pot = bet * 2
    p1_id = data["p1_id"]
    p2_id = data["p2_id"]
    p1_busted = data["p1_busted"]
    p2_busted = data["p2_busted"]
    p1_score = _hand_score_from_dicts(data["p1_hand"])
    p2_score = _hand_score_from_dicts(data["p2_hand"])
    p1_natural = _is_natural(data["p1_hand"])
    p2_natural = _is_natural(data["p2_hand"])

    p1_display = _player_display(data, "p1")
    p2_display = _player_display(data, "p2")

    table = _render_table(data)

    # Определяем победителя
    winner_id = None
    winner_display = ""
    result_line = ""

    if p1_busted and p2_busted:
        # Оба перебрали — ничья
        await score_repo.add_delta(p1_id, chat_id, bet)
        await score_repo.add_delta(p2_id, chat_id, bet)
        sw = p.pluralize(bet)
        result_line = f"\U0001f91d Оба перебрали! Ставки возвращены: по <b>{bet} {sw}</b>."
    elif p1_busted:
        winner_id = p2_id
        winner_display = p2_display
    elif p2_busted:
        winner_id = p1_id
        winner_display = p1_display
    elif p1_natural and not p2_natural:
        winner_id = p1_id
        winner_display = p1_display
        result_line = f"\U0001f3b0 Блекджек! {winner_display} выигрывает <b>{total_pot} {p.pluralize(total_pot)}</b>!"
    elif p2_natural and not p1_natural:
        winner_id = p2_id
        winner_display = p2_display
        result_line = f"\U0001f3b0 Блекджек! {winner_display} выигрывает <b>{total_pot} {p.pluralize(total_pot)}</b>!"
    elif p1_score > p2_score:
        winner_id = p1_id
        winner_display = p1_display
    elif p2_score > p1_score:
        winner_id = p2_id
        winner_display = p2_display
    else:
        # Равный счёт — ничья
        await score_repo.add_delta(p1_id, chat_id, bet)
        await score_repo.add_delta(p2_id, chat_id, bet)
        sw = p.pluralize(bet)
        result_line = f"\U0001f91d Ничья ({p1_score}:{p2_score})! Ставки возвращены: по <b>{bet} {sw}</b>."

    if winner_id and not result_line:
        new_balance = await score_repo.add_delta(winner_id, chat_id, total_pot)
        await stats_repo.add_win(winner_id, chat_id, "blackjack")
        logger.info(
            "BJ game %s: winner %d awarded %d, new balance %d",
            game_id, winner_id, total_pot, new_balance,
        )
        sw = p.pluralize(total_pot)
        result_line = f"\U0001f3c6 {winner_display} выигрывает <b>{total_pot} {sw}</b>!"
    elif winner_id and result_line:
        # Blackjack case — already set result_line
        new_balance = await score_repo.add_delta(winner_id, chat_id, total_pot)
        await stats_repo.add_win(winner_id, chat_id, "blackjack")
        logger.info(
            "BJ game %s: natural BJ winner %d awarded %d, new balance %d",
            game_id, winner_id, total_pot, new_balance,
        )

    return (
        f"\U0001f0cf <b>Блекджек — результат</b>\n\n"
        f"{table}\n\n"
        f"{result_line}"
    )
