"""Callback-хендлеры блекджека: accept, hit, stand, peek.

Каждый ход — compare-and-set по прочитанному снимку игры (store.game_save / game_claim):
второе нажатие по тому же снимку не возьмёт ту же карту ещё раз и не рассчитает игру дважды.
"""

from __future__ import annotations

import random
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.blackjack_service import cards_to_dicts, dicts_to_cards
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import User
from bot.domain.ledger import LedgerKind
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis._games import GameSnapshot
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.blackjack.helpers import (
    _BJ_ACCEPT_LOCK_TTL,
    _BJ_GAME_TTL,
    _BJ_REDIS_BUFFER,
    _DELETE_DELAY,
    _bj_key,
    _deal,
    _format_hand_from_dicts,
    _game_ref,
    _hand_alert,
    _hand_score_from_dicts,
    _other,
    _play_kb,
    _settle_game,
    _side_of,
    _turn_text,
)
from bot.presentation.utils import check_gameban, safe_callback_answer, schedule_delete
from bot.presentation.utils import edit_callback_message as _edit

router = Router(name="blackjack_callbacks")

_NOT_FOUND = "Игра завершена или не найдена."


def _game_id(cb: CallbackQuery) -> str | None:
    parts = (cb.data or "").split(":")
    return parts[2] if len(parts) >= 3 else None


# ── Callback: принять вызов ────────────────────────────────────────────


@router.callback_query(F.data.startswith("bj:accept:"))
@inject
async def cb_bj_accept(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    score_repo: FromDishka[IScoreRepository],
    stats_repo: FromDishka[IUserStatsRepository],
    pluralizer: FromDishka[ScorePluralizer],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    game_id = _game_id(cb)
    if game_id is None:
        await safe_callback_answer(cb)
        return

    chat_id = cb.message.chat.id
    user_id = cb.from_user.id

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, user_id, chat_id, formatter._t)
    if ban_msg:
        await safe_callback_answer(cb, ban_msg, show_alert=True)
        return

    key = _bj_key(chat_id, game_id)
    snapshot = await store.game_load(key)
    if snapshot is None:
        await safe_callback_answer(cb, formatter._t["bj_expired"], show_alert=True)
        return
    data = snapshot.data

    if data["state"] != "lobby":
        await safe_callback_answer(cb, "Игра уже началась.", show_alert=True)
        return

    if data["p1_id"] == user_id:
        await safe_callback_answer(cb, "Нельзя играть с самим собой.", show_alert=True)
        return

    # Лобби истекло: его вот-вот заберёт cleanup-loop и вернёт ставку создателю
    if data.get("expires_at", 0) < time.time():
        await safe_callback_answer(cb, formatter._t["bj_expired"], show_alert=True)
        return

    # Одно принятие на лобби: второй нажавший (или двойное нажатие) не будет списан
    accept_lock = f"bj:lock:accept:{chat_id}:{game_id}"
    if not await store.game_lock(accept_lock, _BJ_ACCEPT_LOCK_TTL):
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
        emoji=SPECIAL_EMOJI.get("bj", "\U0001f0cf"),
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

    data["p2_id"] = user_id
    data["p2_name"] = cb.from_user.full_name or ""
    data["p2_username"] = cb.from_user.username or ""
    data["message_id"] = cb.message.message_id

    # Случайно назначаем, кто p1 и p2 (кто ходит первым)
    if random.choice([True, False]):
        for field in ("id", "name", "username"):
            data[f"p1_{field}"], data[f"p2_{field}"] = data[f"p2_{field}"], data[f"p1_{field}"]

    _deal(data)

    # Лобби → игра, только если лобби ещё ждёт: если его уже забрал cleanup-loop
    # (и вернул ставку создателю), игру не воскрешаем, а ставку принимающего возвращаем
    snapshot = await store.game_replace_if_state(key, "lobby", data, _BJ_GAME_TTL + _BJ_REDIS_BUFFER)
    if snapshot is None:
        await score_repo.add_delta(user_id, chat_id, bet, kind=LedgerKind.REFUND, ref=ref)
        await safe_callback_answer(cb, formatter._t["bj_expired"], show_alert=True)
        return

    side = _side_of(data, user_id)
    if data["p1_done"] and data["p2_done"]:
        # Натуральный блекджек — сразу вскрытие
        if await _finish(cb, store, snapshot, score_repo, stats_repo, p):
            await safe_callback_answer(cb, "\U0001f3b0 Блекджек!")
        return

    await _edit(cb, _turn_text(data, p), _play_kb(game_id))
    await safe_callback_answer(cb, "⚔️ Игра началась!\n\n" + _hand_alert(data, side), show_alert=True)


# ── Ход: общая часть ───────────────────────────────────────────────────


async def _load_turn(cb: CallbackQuery, store: RedisStore) -> tuple[GameSnapshot, str] | None:
    """Игра и сторона нажавшего, если сейчас его ход. Иначе отвечает сам и возвращает None."""
    game_id = _game_id(cb)
    if game_id is None:
        await safe_callback_answer(cb)
        return None

    snapshot = await store.game_load(_bj_key(cb.message.chat.id, game_id))
    if snapshot is None:
        await safe_callback_answer(cb, _NOT_FOUND, show_alert=True)
        return None
    data = snapshot.data
    if data["state"] != "playing":
        await safe_callback_answer(cb, "Игра не активна.", show_alert=True)
        return None

    side = _side_of(data, cb.from_user.id)
    if side is None:
        await safe_callback_answer(cb, "Ты не участник этой игры.", show_alert=True)
        return None
    if side != data["turn"]:
        await safe_callback_answer(cb, "Сейчас не твой ход!")
        return None
    return snapshot, side


async def _finish(
    cb: CallbackQuery,
    store: RedisStore,
    snapshot: GameSnapshot,
    score_repo: IScoreRepository,
    stats_repo: IUserStatsRepository,
    p: ScorePluralizer,
) -> bool:
    """Забрать игру и рассчитать. False — её уже рассчитал другой запрос или цикл истечения."""
    if not await store.game_claim(snapshot):
        await _answer_stale(cb, store, snapshot)
        return False
    text = await _settle_game(snapshot.data, score_repo, stats_repo, p, cb.message.chat.id)
    await _edit(cb, text, None)
    if cb.message.bot:
        schedule_delete(cb.message.bot, cb.message, delay=_DELETE_DELAY)
    return True


async def _answer_stale(cb: CallbackQuery, store: RedisStore, snapshot: GameSnapshot) -> None:
    """Ход посчитан по устаревшему снимку: двойное нажатие или игру уже закончили."""
    if await store.game_load(snapshot.key) is None:
        await safe_callback_answer(cb, _NOT_FOUND, show_alert=True)
    else:
        await safe_callback_answer(cb)  # второе нажатие той же кнопки — ход уже сделан


async def _end_turn(
    cb: CallbackQuery,
    store: RedisStore,
    snapshot: GameSnapshot,
    side: str,
    score_repo: IScoreRepository,
    stats_repo: IUserStatsRepository,
    p: ScorePluralizer,
    alert: str,
) -> None:
    """Сохранить ход. Игрок закончил — ход переходит; закончили оба — вскрытие."""
    data = snapshot.data
    if data[f"{side}_done"] and data[f"{_other(side)}_done"]:
        if await _finish(cb, store, snapshot, score_repo, stats_repo, p):
            await safe_callback_answer(cb, alert, show_alert=True)
        return

    if data[f"{side}_done"]:
        data["turn"] = _other(side)  # перебор не выдаём: ход просто переходит
    data["expires_at"] = time.time() + _BJ_GAME_TTL
    if await store.game_save(snapshot, data, _BJ_GAME_TTL + _BJ_REDIS_BUFFER) is None:
        await _answer_stale(cb, store, snapshot)
        return
    await _edit(cb, _turn_text(data, p), _play_kb(data["game_id"]))
    await safe_callback_answer(cb, alert, show_alert=True)


# ── Callback: Hit ──────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("bj:hit:"))
@inject
async def cb_bj_hit(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_repo: FromDishka[IScoreRepository],
    stats_repo: FromDishka[IUserStatsRepository],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    loaded = await _load_turn(cb, store)
    if loaded is None:
        return
    snapshot, side = loaded
    data = snapshot.data

    deck = dicts_to_cards(data["deck"])
    card = deck.pop()
    data["deck"] = cards_to_dicts(deck)
    data[f"{side}_hand"].append({"rank": card.rank, "suit": card.suit})

    score = _hand_score_from_dicts(data[f"{side}_hand"])
    if score > 21:
        data[f"{side}_busted"] = True
        data[f"{side}_done"] = True
    elif score == 21:
        data[f"{side}_done"] = True  # автоматически «Хватит»

    alert = f"\U0001f0cf Взял {_format_hand_from_dicts([data[f'{side}_hand'][-1]])}.\n" + _hand_alert(data, side)
    await _end_turn(cb, store, snapshot, side, score_repo, stats_repo, pluralizer, alert)


# ── Callback: Stand ────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("bj:stand:"))
@inject
async def cb_bj_stand(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_repo: FromDishka[IScoreRepository],
    stats_repo: FromDishka[IUserStatsRepository],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    loaded = await _load_turn(cb, store)
    if loaded is None:
        return
    snapshot, side = loaded
    snapshot.data[f"{side}_done"] = True
    await _end_turn(cb, store, snapshot, side, score_repo, stats_repo, pluralizer, _hand_alert(snapshot.data, side))


# ── Callback: свои карты ───────────────────────────────────────────────


@router.callback_query(F.data.startswith("bj:peek:"))
@inject
async def cb_bj_peek(cb: CallbackQuery, store: FromDishka[RedisStore]) -> None:
    game_id = _game_id(cb)
    snapshot = await store.game_load(_bj_key(cb.message.chat.id, game_id)) if game_id else None
    if snapshot is None or snapshot.data["state"] != "playing":
        await safe_callback_answer(cb, _NOT_FOUND, show_alert=True)
        return
    side = _side_of(snapshot.data, cb.from_user.id)
    if side is None:
        await safe_callback_answer(cb, "Ты не участник этой игры.", show_alert=True)
        return
    await safe_callback_answer(cb, _hand_alert(snapshot.data, side), show_alert=True)
