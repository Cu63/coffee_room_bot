"""Callback handlers for duel accept/decline buttons."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.stake_service import StakeService
from bot.domain.entities import User
from bot.domain.ledger import LedgerKind
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.duel.game_launchers import (
    _start_bj_from_duel,
    _start_ttt_from_duel,
)
from bot.presentation.handlers.duel.helpers import (
    _duel_key,
    _make_invite_id,
)
from bot.presentation.utils import (
    NO_PREVIEW,
    check_gameban,
    safe_callback_answer,
)

logger = logging.getLogger(__name__)
router = Router(name="duel_callbacks")


# ── Callback: Принять дуэль ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("duel:accept:"))
@inject
async def cb_duel_accept(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    stakes: FromDishka[StakeService],
    stats_repo: FromDishka[IUserStatsRepository],
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
    snapshot = await store.game_load(key)
    if snapshot is None:
        await safe_callback_answer(cb, "⏰ Время вышло или приглашение уже недействительно.", show_alert=True)
        return

    data = snapshot.data

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

    # Списываем ставку целевого в банк дуэли
    ref = f"duel:{invite_id}"
    in_pot = data.get("pot", False)
    result = await stakes.hold(user_id, chat_id, ref, bet, pot=in_pot)
    if not result.success:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(result.current_balance)
        await safe_callback_answer(
            cb,
            f"Недостаточно баллов. Нужно: {bet} {sw}, у тебя: {result.current_balance} {sw_bal}.",
            show_alert=True,
        )
        return

    # Забираем приглашение: если его уже забрал цикл истечения (и вернул ставку создателю)
    # или второе нажатие — возвращаем ставку принимающего
    if not await store.game_delete(key):
        await stakes.pay(user_id, chat_id, ref, bet, kind=LedgerKind.REFUND, pot=in_pot)
        await safe_callback_answer(cb, "⏰ Приглашение уже истекло.", show_alert=True)
        return

    game_id = _make_invite_id()

    if game == "ttt":
        await _start_ttt_from_duel(cb, data, game_id, chat_id, bet, store, p)
    else:
        await _start_bj_from_duel(cb, data, game_id, chat_id, bet, store, stakes, stats_repo, p)


# ── Callback: Отказаться от дуэли ───────────────────────────────────────


@router.callback_query(F.data.startswith("duel:decline:"))
@inject
async def cb_duel_decline(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    stakes: FromDishka[StakeService],
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
    snapshot = await store.game_load(key)
    if snapshot is None:
        await safe_callback_answer(cb, "⏰ Приглашение уже истекло.", show_alert=True)
        return

    data = snapshot.data

    # Отказаться может только целевой игрок
    if user_id != data["target_id"]:
        if user_id == data["challenger_id"]:
            await safe_callback_answer(cb, "Ты не можешь отклонить свой собственный вызов.", show_alert=False)
        else:
            await safe_callback_answer(cb, "Этот вызов предназначен не тебе.", show_alert=True)
        return

    # Забираем приглашение: вернуть ставку должен ровно один — отказ или цикл истечения
    if not await store.game_delete(key):
        await safe_callback_answer(cb, "⏰ Приглашение уже истекло.", show_alert=True)
        return

    # Возвращаем ставку создателю
    bet = data["bet"]
    challenger_id = data["challenger_id"]
    await stakes.pay(
        challenger_id, chat_id, f"duel:{invite_id}", bet, kind=LedgerKind.REFUND, pot=data.get("pot", False)
    )

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
            f"❌ {decliner_display} отказался от дуэли с {challenger_display}.\n\nСтавка возвращена.",
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
