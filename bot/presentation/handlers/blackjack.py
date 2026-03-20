"""Хендлер дуэльного блекджека: /bj <ставка>.

Правила:
  1. /bj <ставка> — создать лобби, ставка списывается сразу
  2. Второй игрок принимает (ставка тоже списывается)
  3. Обоим раздаётся по 2 карты из общей колоды
  4. Случайно выбирается, кто ходит первым
  5. Каждый игрок по очереди: «Ещё» (взять карту) или «Хватит» (остановиться)
  6. При переборе (>21) ход автоматически переходит к сопернику
  7. При 21 очках — автоматически «Хватит»
  8. После того как оба закончили — сравнение очков
  9. Победитель забирает весь банк (2× ставки)
  10. Ничья — ставки возвращаются
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

from bot.application.blackjack_service import (
    build_deck,
    cards_to_dicts,
    dicts_to_cards,
    format_hand,
    hand_score,
)
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import (
    NO_PREVIEW,
    check_gameban,
    reply_and_delete,
    safe_callback_answer,
    schedule_delete,
)

logger = logging.getLogger(__name__)
router = Router(name="blackjack")

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
                InlineKeyboardButton(text="🃏 Ещё", callback_data=f"bj:hit:{game_id}"),
                InlineKeyboardButton(
                    text="🛑 Хватит", callback_data=f"bj:stand:{game_id}"
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
        suffix = " 💥"
    elif done:
        suffix = " ✋"
    return f"🎴 <b>{name}</b> [{score}{suffix}]:  {cards}"


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
        f"🃏 <b>Блекджек — дуэль</b>\n"
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
        return "⏰ Игра была завершена по таймауту. Ставки возвращены."

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
        result_line = f"🤝 Оба перебрали! Ставки возвращены: по <b>{bet} {sw}</b>."
    elif p1_busted:
        winner_id = p2_id
        winner_display = p2_display
    elif p2_busted:
        winner_id = p1_id
        winner_display = p1_display
    elif p1_natural and not p2_natural:
        winner_id = p1_id
        winner_display = p1_display
        result_line = f"🎰 Блекджек! {winner_display} выигрывает <b>{total_pot} {p.pluralize(total_pot)}</b>!"
    elif p2_natural and not p1_natural:
        winner_id = p2_id
        winner_display = p2_display
        result_line = f"🎰 Блекджек! {winner_display} выигрывает <b>{total_pot} {p.pluralize(total_pot)}</b>!"
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
        result_line = f"🤝 Ничья ({p1_score}:{p2_score})! Ставки возвращены: по <b>{bet} {sw}</b>."

    if winner_id and not result_line:
        new_balance = await score_repo.add_delta(winner_id, chat_id, total_pot)
        await stats_repo.add_win(winner_id, chat_id, "blackjack")
        logger.info(
            "BJ game %s: winner %d awarded %d, new balance %d",
            game_id, winner_id, total_pot, new_balance,
        )
        sw = p.pluralize(total_pot)
        result_line = f"🏆 {winner_display} выигрывает <b>{total_pot} {sw}</b>!"
    elif winner_id and result_line:
        # Blackjack case — already set result_line
        new_balance = await score_repo.add_delta(winner_id, chat_id, total_pot)
        await stats_repo.add_win(winner_id, chat_id, "blackjack")
        logger.info(
            "BJ game %s: natural BJ winner %d awarded %d, new balance %d",
            game_id, winner_id, total_pot, new_balance,
        )

    return (
        f"🃏 <b>Блекджек — результат</b>\n\n"
        f"{table}\n\n"
        f"{result_line}"
    )


# ── /bj <ставка> ──────────────────────────────────────────────────────


@router.message(Command("bj"))
@inject
async def cmd_blackjack(
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

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, message.from_user.id, message.chat.id, formatter._t)
    if ban_msg:
        await reply_and_delete(message, ban_msg)
        return

    args = (message.text or "").split()[1:]
    bjc = config.blackjack
    p = pluralizer

    if not args:
        sw_max = p.pluralize(bjc.max_bet)
        await reply_and_delete(
            message,
            formatter._t["bj_usage"].format(
                min_bet=bjc.min_bet,
                max_bet=bjc.max_bet,
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

    if bet < bjc.min_bet:
        sw = p.pluralize(bjc.min_bet)
        await reply_and_delete(message, f"❌ Минимальная ставка: {bjc.min_bet} {sw}.")
        return

    if bet > bjc.max_bet:
        sw = p.pluralize(bjc.max_bet)
        await reply_and_delete(message, f"❌ Максимальная ставка: {bjc.max_bet} {sw}.")
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

    # Проверяем баланс и списываем ставку
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=bet,
        emoji=SPECIAL_EMOJI.get("bj", "🃏"),
    )
    if not result.success:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(result.current_balance)
        await reply_and_delete(
            message,
            formatter._t["bj_not_enough"].format(
                cost=bet,
                score_word=sw,
                balance=result.current_balance,
                score_word_balance=sw_bal,
            ),
        )
        return

    # Создаём лобби
    game_id = _make_game_id()
    display = user_link(
        message.from_user.username, message.from_user.full_name or "", user_id
    )
    sw_bet = p.pluralize(bet)

    data = {
        "game_id": game_id,
        "state": "lobby",
        "p1_id": user_id,
        "p1_name": message.from_user.full_name or "",
        "p1_username": message.from_user.username or "",
        "p2_id": None,
        "p2_name": "",
        "p2_username": "",
        "deck": [],
        "p1_hand": [],
        "p2_hand": [],
        "p1_done": False,
        "p2_done": False,
        "p1_busted": False,
        "p2_busted": False,
        "turn": "",
        "bet": bet,
        "chat_id": chat_id,
        "message_id": 0,
        "created_at": time.time(),
        "expires_at": time.time() + _BJ_LOBBY_TTL,
    }

    key = _bj_key(chat_id, game_id)
    await store._r.set(key, json.dumps(data), ex=_BJ_LOBBY_TTL)

    lobby_text = formatter._t["bj_lobby"].format(
        user=display,
        bet=bet,
        score_word=sw_bet,
    )
    sent = await message.answer(
        lobby_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_lobby_kb(game_id),
    )

    # Сохраняем message_id
    data["message_id"] = sent.message_id
    await store._r.set(key, json.dumps(data), ex=_BJ_LOBBY_TTL)


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
        emoji=SPECIAL_EMOJI.get("bj", "🃏"),
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
        await safe_callback_answer(cb, "🎰 Блекджек!")
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
        await safe_callback_answer(cb, "⚔️ Игра завершена!")
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

    await safe_callback_answer(cb, "⚔️ Игра началась!")


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
            await safe_callback_answer(cb, "💥 Перебор!")
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
        await safe_callback_answer(cb, "💥 Перебор!")
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
            await safe_callback_answer(cb, "🎯 21!")
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
        await safe_callback_answer(cb, "🎯 21!")
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
        await safe_callback_answer(cb, "✋ Хватит!")
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
    await safe_callback_answer(cb, "✋ Хватит!")
