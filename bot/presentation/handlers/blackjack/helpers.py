"""Вспомогательные функции, константы, клавиатуры и рендеринг для блекджека."""

from __future__ import annotations

import logging
import random
import time

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
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.stake_service import StakeService
from bot.domain.ledger import LedgerKind
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import user_link

logger = logging.getLogger(__name__)

# ── Константы ──────────────────────────────────────────────────────────
_BJ_PREFIX = "bj:duel:"
_BJ_LOBBY_TTL = 300  # 5 минут на принятие вызова
_BJ_GAME_TTL = 600  # 10 минут на ход (expires_at продлевается каждым ходом)
_BJ_ACCEPT_LOCK_TTL = 30
_DELETE_DELAY = 120  # задержка удаления результата
# Запас Redis-TTL поверх логического expires_at, чтобы bj_cleanup_loop
# успел увидеть просроченный ключ и вернуть ставки до того, как Redis
# уничтожит его сам. Без буфера ключ удалялся одновременно с истечением
# expires_at и рефанд просто не происходил.
_BJ_REDIS_BUFFER = 3600  # 1 час

_CARD_BACK = "\U0001f0a0"


# ── Утилиты ────────────────────────────────────────────────────────────


def _bj_key(chat_id: int, game_id: str) -> str:
    return f"{_BJ_PREFIX}{chat_id}:{game_id}"


def _make_game_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


def _game_ref(data: dict) -> str:
    """Метка ставок и выплат в журнале: у игры из /duel — метка приглашения."""
    return data.get("ref") or f"bj:{data['game_id']}"


def _other(side: str) -> str:
    return "p2" if side == "p1" else "p1"


def _hand_score_from_dicts(hand: list[dict]) -> int:
    return hand_score(dicts_to_cards(hand))


def _format_hand_from_dicts(hand: list[dict]) -> str:
    return format_hand(dicts_to_cards(hand))


def _is_natural(hand: list[dict]) -> bool:
    return len(hand) == 2 and _hand_score_from_dicts(hand) == 21


def _deal(data: dict) -> None:
    """Раздать по две карты и начать игру: первым ходит p1.

    Натуральный блекджек (21 с двух карт) у кого-то из игроков сразу заканчивает игру.
    """
    deck = build_deck()
    data["p1_hand"] = cards_to_dicts([deck.pop(), deck.pop()])
    data["p2_hand"] = cards_to_dicts([deck.pop(), deck.pop()])
    data["deck"] = cards_to_dicts(deck)
    data["state"] = "playing"
    data["turn"] = "p1"
    data["expires_at"] = time.time() + _BJ_GAME_TTL
    if _is_natural(data["p1_hand"]) or _is_natural(data["p2_hand"]):
        data["p1_done"] = data["p2_done"] = True


def _side_of(data: dict, user_id: int) -> str | None:
    for side in ("p1", "p2"):
        if data.get(f"{side}_id") == user_id:
            return side
    return None


def _winner(data: dict) -> str | None:
    """'p1' / 'p2' или None — ничья (в том числе оба перебрали)."""
    p1_busted, p2_busted = data["p1_busted"], data["p2_busted"]
    if p1_busted and p2_busted:
        return None
    if p1_busted or p2_busted:
        return "p2" if p1_busted else "p1"
    p1_natural, p2_natural = _is_natural(data["p1_hand"]), _is_natural(data["p2_hand"])
    if p1_natural != p2_natural:
        return "p1" if p1_natural else "p2"
    p1_score, p2_score = _hand_score_from_dicts(data["p1_hand"]), _hand_score_from_dicts(data["p2_hand"])
    if p1_score == p2_score:
        return None
    return "p1" if p1_score > p2_score else "p2"


# ── Клавиатуры ─────────────────────────────────────────────────────────


def _lobby_kb(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Принять вызов",
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
                InlineKeyboardButton(text="\U0001f6d1 Хватит", callback_data=f"bj:stand:{game_id}"),
            ],
            [InlineKeyboardButton(text="\U0001f440 Мои карты", callback_data=f"bj:peek:{game_id}")],
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
    name: str,
    hand: list[dict],
    *,
    done: bool = False,
    busted: bool = False,
    hidden: bool = False,
) -> str:
    if hidden:
        # Видно только, сколько карт и остановился ли игрок: перебор не выдаём
        backs = "  ".join(_CARD_BACK for _ in hand)
        suffix = " ✋" if done else ""
        return f"\U0001f3b4 <b>{name}</b> [?{suffix}]:  {backs}"
    score = _hand_score_from_dicts(hand)
    cards = _format_hand_from_dicts(hand)
    suffix = ""
    if busted:
        suffix = " \U0001f4a5"
    elif done:
        suffix = " ✋"
    return f"\U0001f3b4 <b>{name}</b> [{score}{suffix}]:  {cards}"


def _render_table(
    data: dict,
    *,
    result_line: str = "",
    reveal: bool = False,
) -> str:
    """Стол в общем чате. До вскрытия обе руки скрыты: свои карты игрок смотрит кнопкой."""
    lines = [
        _render_hand_line(
            _player_display(data, side),
            data[f"{side}_hand"],
            done=data.get(f"{side}_done", False),
            busted=data.get(f"{side}_busted", False),
            hidden=not reveal,
        )
        for side in ("p1", "p2")
    ]
    if result_line:
        lines.append("")
        lines.append(result_line)
    return "\n".join(lines)


def _turn_text(data: dict, p: ScorePluralizer) -> str:
    """Полный текст сообщения во время хода."""
    bet = data["bet"]
    sw = p.pluralize(bet)
    turn_display = _player_display(data, data["turn"])

    table = _render_table(data)
    return (
        f"\U0001f0cf <b>Блекджек — дуэль</b>\nСтавка: <b>{bet} {sw}</b>\n\n{table}\n\n"
        f"Ходит: {turn_display}\n<i>Свои карты — кнопкой «Мои карты»: их видишь только ты.</i>"
    )


def _hand_alert(data: dict, side: str) -> str:
    """Рука игрока для всплывающего окна — его видит только нажавший."""
    hand = data[f"{side}_hand"]
    text = f"\U0001f0cf Твои карты: {_format_hand_from_dicts(hand)} — {_hand_score_from_dicts(hand)}"
    if data.get(f"{side}_busted"):
        text += "\n\U0001f4a5 Перебор! Соперник об этом не знает."
    elif data.get(f"{side}_done"):
        text += "\n✋ Ты остановился."
    return text


# ── Логика завершения ──────────────────────────────────────────────────


async def _settle_game(
    data: dict,
    stakes: StakeService,
    stats_repo: IUserStatsRepository,
    p: ScorePluralizer,
    chat_id: int,
) -> str:
    """Выплатить из банка игры по итогам и вернуть текст результата.

    Вызывать только тому, кто забрал игру (store.game_claim / новая игра без сохранения).
    Второй расчёт упрётся в пустой банк (PotOverdraft) и откатится.
    """
    bet = data["bet"]
    prize = bet * 2
    ref = _game_ref(data)
    in_pot = data.get("pot", False)
    table = _render_table(data, reveal=True)
    winner = _winner(data)

    if winner is None:
        for side in ("p1", "p2"):
            await stakes.pay(data[f"{side}_id"], chat_id, ref, bet, kind=LedgerKind.REFUND, pot=in_pot)
        sw = p.pluralize(bet)
        if data["p1_busted"] and data["p2_busted"]:
            result_line = f"\U0001f91d Оба перебрали! Ставки возвращены: по <b>{bet} {sw}</b>."
        else:
            score = _hand_score_from_dicts(data["p1_hand"])
            result_line = f"\U0001f91d Ничья ({score}:{score})! Ставки возвращены: по <b>{bet} {sw}</b>."
    else:
        winner_id = data[f"{winner}_id"]
        new_balance = await stakes.pay(winner_id, chat_id, ref, prize, kind=LedgerKind.PRIZE, pot=in_pot)
        await stats_repo.add_win(winner_id, chat_id, "blackjack")
        logger.info("BJ game %s: winner %d awarded %d, new balance %d", data["game_id"], winner_id, prize, new_balance)
        sw = p.pluralize(prize)
        winner_display = _player_display(data, winner)
        loser_natural = _is_natural(data[f"{_other(winner)}_hand"])
        if _is_natural(data[f"{winner}_hand"]) and not loser_natural:
            result_line = f"\U0001f3b0 Блекджек! {winner_display} выигрывает <b>{prize} {sw}</b>!"
        else:
            result_line = f"\U0001f3c6 {winner_display} выигрывает <b>{prize} {sw}</b>!"

    return f"\U0001f0cf <b>Блекджек — результат</b>\n\n{table}\n\n{result_line}"
