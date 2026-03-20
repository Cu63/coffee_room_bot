"""Callback-хендлеры блекджека: accept, hit, stand."""

from __future__ import annotations

import json
import random
import time

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.blackjack_service import (
    build_deck,
    cards_to_dicts,
    dicts_to_cards,
)
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.blackjack.helpers import (
    _BJ_GAME_TTL,
    _bj_key,
    _hand_score_from_dicts,
    _is_natural,
    _play_kb,
    _resolve_game,
    _turn_text,
)
from bot.presentation.utils import (
    NO_PREVIEW,
    check_gameban,
    safe_callback_answer,
    schedule_delete,
)

router = Router(name="blackjack_callbacks")

_DELETE_DELAY = 120  # задержка удаления результата


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

    key = _bj_key(chat_id, game_id)

    raw = await store._r.get(key)
    if raw is None:
        await safe_callback_answer(cb, formatter._t["bj_expired"], show_alert=True)
        return

    data = json.loads(raw)

    if data["state"] != "lobby":
        await safe_callback_answer(cb, "Игра уже началась.", show_alert=True)
        return

    if data["p1_id"] == user_id:
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
        emoji=SPECIAL_EMOJI.get("bj", "\U0001f0cf"),
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

    # Заполняем данные второго игрока
    data["p2_id"] = user_id
    data["p2_name"] = cb.from_user.full_name or ""
    data["p2_username"] = cb.from_user.username or ""

    # Случайно назначаем, кто p1 и p2 (кто ходит первым)
    if random.choice([True, False]):
        # Меняем местами: принимающий становится p1, создатель — p2
        data["p1_id"], data["p2_id"] = data["p2_id"], data["p1_id"]
        data["p1_name"], data["p2_name"] = data["p2_name"], data["p1_name"]
        data["p1_username"], data["p2_username"] = (
            data["p2_username"],
            data["p1_username"],
        )

    # Раздаём карты
    deck = build_deck()
    data["p1_hand"] = cards_to_dicts([deck.pop(), deck.pop()])
    data["p2_hand"] = cards_to_dicts([deck.pop(), deck.pop()])
    data["deck"] = cards_to_dicts(deck)
    data["state"] = "playing"
    data["turn"] = "p1"  # p1 ходит первым
    data["expires_at"] = time.time() + _BJ_GAME_TTL

    # Проверяем натуральные блекджеки
    p1_natural = _is_natural(data["p1_hand"])
    p2_natural = _is_natural(data["p2_hand"])

    if p1_natural or p2_natural:
        # Мгновенное завершение
        data["p1_done"] = True
        data["p2_done"] = True

        text = await _resolve_game(data, store, score_repo, stats_repo, p, chat_id)
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
        await safe_callback_answer(cb, "\U0001f3b0 Блекджек!")
        return

    # Проверяем автостенд на 21 для первого игрока
    if _hand_score_from_dicts(data["p1_hand"]) == 21:
        data["p1_done"] = True
        data["turn"] = "p2"
        # Проверяем и для второго
        if _hand_score_from_dicts(data["p2_hand"]) == 21:
            data["p2_done"] = True

    # Если оба уже закончили (обоим раздали по 21 не-натуральный — маловероятно)
    if data["p1_done"] and data["p2_done"]:
        text = await _resolve_game(data, store, score_repo, stats_repo, p, chat_id)
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
        await safe_callback_answer(cb, "\u2694\ufe0f Игра завершена!")
        return

    # Сохраняем и показываем
    await store._r.set(key, json.dumps(data), ex=_BJ_GAME_TTL)

    text = _turn_text(data, p)
    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_play_kb(game_id),
        )
    except Exception:
        pass

    await safe_callback_answer(cb, "\u2694\ufe0f Игра началась!")


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
    parts = cb.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(cb)
        return

    game_id = parts[2]
    chat_id = cb.message.chat.id
    user_id = cb.from_user.id
    key = _bj_key(chat_id, game_id)

    raw = await store._r.get(key)
    if raw is None:
        await safe_callback_answer(cb, "Игра завершена или не найдена.", show_alert=True)
        return

    data = json.loads(raw)

    if data["state"] != "playing":
        await safe_callback_answer(cb, "Игра не активна.", show_alert=True)
        return

    turn = data["turn"]
    active_id = data[f"{turn}_id"]

    if user_id != active_id:
        # Проверяем, является ли пользователь вторым игроком
        other = "p2" if turn == "p1" else "p1"
        if user_id == data[f"{other}_id"]:
            await safe_callback_answer(cb, "Сейчас не твой ход!", show_alert=False)
        else:
            await safe_callback_answer(
                cb, "Ты не участник этой игры.", show_alert=True
            )
        return

    p = pluralizer

    # Берём карту
    deck = dicts_to_cards(data["deck"])
    card = deck.pop()
    data["deck"] = cards_to_dicts(deck)
    data[f"{turn}_hand"].append({"rank": card.rank, "suit": card.suit})

    score = _hand_score_from_dicts(data[f"{turn}_hand"])

    # Обновляем expires_at при каждом действии, чтобы cleanup loop не забрал активную игру
    data["expires_at"] = time.time() + _BJ_GAME_TTL

    if score > 21:
        # Перебор
        data[f"{turn}_busted"] = True
        data[f"{turn}_done"] = True

        if data[f"{'p2' if turn == 'p1' else 'p1'}_done"]:
            # Оба закончили — результат
            text = await _resolve_game(
                data, store, score_repo, stats_repo, p, chat_id
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
            await safe_callback_answer(cb, "\U0001f4a5 Перебор!")
            return

        # Переход хода к сопернику
        data["turn"] = "p2" if turn == "p1" else "p1"
        await store._r.set(key, json.dumps(data), ex=_BJ_GAME_TTL)

        text = _turn_text(data, p)
        try:
            await cb.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
                reply_markup=_play_kb(game_id),
            )
        except Exception:
            pass
        await safe_callback_answer(cb, "\U0001f4a5 Перебор!")
        return

    if score == 21:
        # Автоматический Stand
        data[f"{turn}_done"] = True

        other = "p2" if turn == "p1" else "p1"
        if data[f"{other}_done"]:
            # Оба закончили — результат
            text = await _resolve_game(
                data, store, score_repo, stats_repo, p, chat_id
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
            await safe_callback_answer(cb, "\U0001f3af 21!")
            return

        # Переход хода
        data["turn"] = other
        await store._r.set(key, json.dumps(data), ex=_BJ_GAME_TTL)

        text = _turn_text(data, p)
        try:
            await cb.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
                reply_markup=_play_kb(game_id),
            )
        except Exception:
            pass
        await safe_callback_answer(cb, "\U0001f3af 21!")
        return

    # Продолжаем — тот же игрок ходит
    await store._r.set(key, json.dumps(data), ex=_BJ_GAME_TTL)

    text = _turn_text(data, p)
    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_play_kb(game_id),
        )
    except Exception:
        pass
    await safe_callback_answer(cb)


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
    parts = cb.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(cb)
        return

    game_id = parts[2]
    chat_id = cb.message.chat.id
    user_id = cb.from_user.id
    key = _bj_key(chat_id, game_id)

    raw = await store._r.get(key)
    if raw is None:
        await safe_callback_answer(cb, "Игра завершена или не найдена.", show_alert=True)
        return

    data = json.loads(raw)

    if data["state"] != "playing":
        await safe_callback_answer(cb, "Игра не активна.", show_alert=True)
        return

    turn = data["turn"]
    active_id = data[f"{turn}_id"]

    if user_id != active_id:
        other = "p2" if turn == "p1" else "p1"
        if user_id == data[f"{other}_id"]:
            await safe_callback_answer(cb, "Сейчас не твой ход!", show_alert=False)
        else:
            await safe_callback_answer(
                cb, "Ты не участник этой игры.", show_alert=True
            )
        return

    p = pluralizer
    data[f"{turn}_done"] = True

    # Обновляем expires_at при каждом действии
    data["expires_at"] = time.time() + _BJ_GAME_TTL

    other = "p2" if turn == "p1" else "p1"
    if data[f"{other}_done"]:
        # Оба закончили — результат
        text = await _resolve_game(data, store, score_repo, stats_repo, p, chat_id)
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
        await safe_callback_answer(cb, "\u270b Хватит!")
        return

    # Переход хода к сопернику
    data["turn"] = other
    await store._r.set(key, json.dumps(data), ex=_BJ_GAME_TTL)

    text = _turn_text(data, p)
    try:
        await cb.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_play_kb(game_id),
        )
    except Exception:
        pass
    await safe_callback_answer(cb, "\u270b Хватит!")
