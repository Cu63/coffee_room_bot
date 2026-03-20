"""Command handler for /duel — creating a duel invitation."""

from __future__ import annotations

import json
import logging
import time

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

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
    schedule_delete_id,
)

from bot.presentation.handlers.duel.helpers import (
    _DUEL_INVITE_TTL,
    _DUEL_TIMEOUT,
    _SUPPORTED_GAMES,
    _duel_key,
    _invite_kb,
    _make_invite_id,
)

logger = logging.getLogger(__name__)
router = Router(name="duel_start")


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
