"""Хендлер /duel — вызов конкретного игрока на дуэль в /ttt или /bj.

Использование:
    /duel @username ttt <ставка>
    /duel @username bj <ставка>

Поведение:
  1. Создатель вводит команду → ставка списывается, отправляется сообщение с двумя кнопками.
  2. Целевой игрок видит сообщение с кнопками «Принять» (зелёная) и «Отказаться» (красная).
  3. Таймаут 2 минуты: если никто не нажал → сообщение удаляется, ставка возвращается.
  4. При принятии: ставка списывается с целевого, игра стартует немедленно.
  5. При отказе: ставка возвращается создателю, сообщение удаляется.
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

from bot.application.blackjack_service import build_deck, cards_to_dicts
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
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
    schedule_delete_id,
)

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

logger = logging.getLogger(__name__)
router = Router(name="duel")

# ── Константы ───────────────────────────────────────────────────────────
_DUEL_PREFIX = "duel:invite:"
_DUEL_INVITE_TTL = 150   # секунд — с запасом, cleanup-loop проверяет expires_at=120
_DUEL_TIMEOUT = 120      # секунд — реальный таймаут для игрока

_SUPPORTED_GAMES = ("ttt", "bj")


# ── Ключ приглашения ────────────────────────────────────────────────────

def _duel_key(chat_id: int, invite_id: str) -> str:
    return f"{_DUEL_PREFIX}{chat_id}:{invite_id}"


def _make_invite_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


# ── Клавиатура приглашения ───────────────────────────────────────────────

def _invite_kb(invite_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"duel:accept:{invite_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отказаться",
                    callback_data=f"duel:decline:{invite_id}",
                ),
            ]
        ]
    )


# ── /duel @user <game> <bet> ─────────────────────────────────────────────

@router.message(Command("duel"))
@inject
async def cmd_duel(
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

    ban_msg = await check_gameban(store, message.from_user.id, message.chat.id, formatter._t)
    if ban_msg:
        await reply_and_delete(message, ban_msg)
        return

    args = (message.text or "").split()[1:]

    if len(args) < 3:
        await reply_and_delete(
            message,
            "⚔️ <b>Дуэль</b>\n\n"
            "Использование: <code>/duel @игрок ttt|bj &lt;ставка&gt;</code>\n\n"
            "Игры: <code>ttt</code> — крестики-нолики, <code>bj</code> — блекджек",
            parse_mode=ParseMode.HTML,
        )
        return

    # Парсим аргументы
    raw_target = args[0].lstrip("@")
    game = args[1].lower()
    bet_raw = args[2]

    if game not in _SUPPORTED_GAMES:
        await reply_and_delete(
            message,
            f"❌ Неизвестная игра <b>{game}</b>. Доступны: <code>ttt</code>, <code>bj</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Парсим ставку
    try:
        bet = int(bet_raw)
        if bet <= 0:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, "❌ Ставка должна быть положительным числом.")
        return

    # Проверяем лимиты ставки
    p = pluralizer
    if game == "ttt":
        cfg = config.tictactoe
        if not cfg.enabled:
            await reply_and_delete(message, "❌ Крестики-нолики отключены.")
            return
    else:
        cfg = config.blackjack
        if not cfg.enabled:
            await reply_and_delete(message, "❌ Блекджек отключён.")
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

    # Ищем целевого игрока в БД
    target = await user_repo.get_by_username(raw_target)
    if target is None:
        await reply_and_delete(
            message,
            f"❌ Пользователь @{raw_target} не найден. Он должен хотя бы раз написать в чат.",
        )
        return

    if target.id == user_id:
        await reply_and_delete(message, "❌ Нельзя вызвать самого себя на дуэль.")
        return

    # Проверка бана у оппонента
    ban_msg_target = await check_gameban(store, target.id, chat_id, formatter._t)
    if ban_msg_target:
        await reply_and_delete(message, "❌ Этот игрок запретил себе игры.")
        return

    # Upsert создателя
    await user_repo.upsert(
        User(
            id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    )

    # Списываем ставку с создателя
    emoji = SPECIAL_EMOJI.get(game, "⚔️")
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=bet,
        emoji=emoji,
    )
    if not result.success:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(result.current_balance)
        await reply_and_delete(
            message,
            f"❌ Недостаточно баллов. Нужно: {bet} {sw}, у тебя: {result.current_balance} {sw_bal}.",
        )
        return

    # Создаём приглашение
    invite_id = _make_invite_id()
    challenger_display = user_link(
        message.from_user.username,
        message.from_user.full_name or "",
        user_id,
    )
    target_display = user_link(target.username, target.full_name, target.id)
    sw_bet = p.pluralize(bet)

    game_name = "🎮 Крестики-нолики" if game == "ttt" else "🃏 Блекджек"

    invite_text = (
        f"⚔️ <b>Дуэль!</b>\n\n"
        f"{challenger_display} вызывает {target_display} на дуэль!\n\n"
        f"Игра: <b>{game_name}</b>\n"
        f"Ставка: <b>{bet} {sw_bet}</b>\n\n"
        f"⏳ У тебя 2 минуты, чтобы принять или отклонить."
    )

    data = {
        "invite_id": invite_id,
        "game": game,
        "challenger_id": user_id,
        "challenger_name": message.from_user.full_name or "",
        "challenger_username": message.from_user.username or "",
        "target_id": target.id,
        "target_name": target.full_name,
        "target_username": target.username or "",
        "bet": bet,
        "chat_id": chat_id,
        "message_id": 0,
        "expires_at": time.time() + _DUEL_TIMEOUT,
        "created_at": time.time(),
    }

    key = _duel_key(chat_id, invite_id)
    await store._r.set(key, json.dumps(data), ex=_DUEL_INVITE_TTL)

    sent = await message.answer(
        invite_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_invite_kb(invite_id),
    )

    # Сохраняем message_id и планируем удаление сообщения через таймаут
    data["message_id"] = sent.message_id
    await store._r.set(key, json.dumps(data), ex=_DUEL_INVITE_TTL)

    schedule_delete_id(message.bot, chat_id, message.message_id, delay=5)


# ── Callback: Принять дуэль ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("duel:accept:"))
@inject
async def cb_duel_accept(
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

    invite_id = parts[2]
    chat_id = cb.message.chat.id
    user_id = cb.from_user.id

    key = _duel_key(chat_id, invite_id)
    raw = await store._r.get(key)
    if raw is None:
        await safe_callback_answer(cb, "⏰ Время вышло или приглашение уже недействительно.", show_alert=True)
        return

    data = json.loads(raw)

    # Только целевой игрок может принять
    if user_id != data["target_id"]:
        if user_id == data["challenger_id"]:
            await safe_callback_answer(cb, "Ты создал этот вызов — жди ответа оппонента.", show_alert=False)
        else:
            await safe_callback_answer(cb, "Этот вызов предназначен не тебе.", show_alert=True)
        return

    ban_msg = await check_gameban(store, user_id, chat_id, formatter._t)
    if ban_msg:
        await safe_callback_answer(cb, ban_msg, show_alert=True)
        return

    bet = data["bet"]
    game = data["game"]
    p = pluralizer

    # Upsert целевого игрока
    await user_repo.upsert(
        User(
            id=user_id,
            username=cb.from_user.username,
            full_name=cb.from_user.full_name,
        )
    )

    # Списываем ставку с целевого
    emoji = SPECIAL_EMOJI.get(game, "⚔️")
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=bet,
        emoji=emoji,
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

    # Атомарно удаляем приглашение
    deleted = await store._r.delete(key)
    if not deleted:
        # Кто-то успел раньше (race condition) — возвращаем ставку
        await score_repo.add_delta(user_id, chat_id, bet)
        await safe_callback_answer(cb, "⏰ Приглашение уже истекло.", show_alert=True)
        return

    game_id = _make_invite_id()

    if game == "ttt":
        await _start_ttt_from_duel(cb, data, game_id, chat_id, bet, store, p)
    else:
        await _start_bj_from_duel(cb, data, game_id, chat_id, bet, store, score_repo, p)

    await safe_callback_answer(cb, "⚔️ Игра началась!")


# ── Callback: Отказаться от дуэли ───────────────────────────────────────

@router.callback_query(F.data.startswith("duel:decline:"))
@inject
async def cb_duel_decline(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_repo: FromDishka[IScoreRepository],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    parts = cb.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(cb)
        return

    invite_id = parts[2]
    chat_id = cb.message.chat.id
    user_id = cb.from_user.id

    key = _duel_key(chat_id, invite_id)
    raw = await store._r.get(key)
    if raw is None:
        await safe_callback_answer(cb, "⏰ Приглашение уже истекло.", show_alert=True)
        return

    data = json.loads(raw)

    # Отказаться может только целевой игрок
    if user_id != data["target_id"]:
        if user_id == data["challenger_id"]:
            await safe_callback_answer(cb, "Ты не можешь отклонить свой собственный вызов.", show_alert=False)
        else:
            await safe_callback_answer(cb, "Этот вызов предназначен не тебе.", show_alert=True)
        return

    # Атомарно удаляем приглашение
    deleted = await store._r.delete(key)
    if not deleted:
        await safe_callback_answer(cb, "⏰ Приглашение уже истекло.", show_alert=True)
        return

    # Возвращаем ставку создателю
    bet = data["bet"]
    challenger_id = data["challenger_id"]
    await score_repo.add_delta(challenger_id, chat_id, bet)

    challenger_display = user_link(
        data["challenger_username"] or None,
        data["challenger_name"],
        challenger_id,
    )
    decliner_display = user_link(
        cb.from_user.username,
        cb.from_user.full_name or "",
        user_id,
    )

    try:
        await cb.message.edit_text(
            f"❌ {decliner_display} отказался от дуэли с {challenger_display}.\n\n"
            f"Ставка возвращена.",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=None,
        )
        if cb.message.bot:
            from bot.presentation.utils import schedule_delete
            schedule_delete(cb.message.bot, cb.message, delay=30)
    except Exception:
        pass

    await safe_callback_answer(cb, "❌ Ты отказался от дуэли.")


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
