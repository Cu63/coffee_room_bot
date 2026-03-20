"""Callback handlers for duel accept/decline buttons."""

from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import (
    NO_PREVIEW,
    check_gameban,
    safe_callback_answer,
)

from bot.presentation.handlers.duel.helpers import (
    _duel_key,
    _make_invite_id,
)
from bot.presentation.handlers.duel.game_launchers import (
    _start_bj_from_duel,
    _start_ttt_from_duel,
)

logger = logging.getLogger(__name__)
router = Router(name="duel_callbacks")


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
