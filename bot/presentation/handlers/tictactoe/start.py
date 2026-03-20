"""Хендлер /ttt — создание игры."""

from __future__ import annotations

import json
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
from bot.presentation.handlers.tictactoe.game_logic import (
    _TTT_LOBBY_TTL,
    _lobby_kb,
    _make_game_id,
    _ttt_key,
)
from bot.presentation.utils import NO_PREVIEW, check_gameban, reply_and_delete

router = Router(name="ttt_start")


@router.message(Command("ttt"))
@inject
async def cmd_ttt(
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
    cfg = config.tictactoe
    p = pluralizer

    if not args:
        sw_max = p.pluralize(cfg.max_bet)
        await reply_and_delete(
            message,
            formatter._t["ttt_usage"].format(
                min_bet=cfg.min_bet,
                max_bet=cfg.max_bet,
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

    # Upsert пользователя
    await user_repo.upsert(
        User(
            id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    )

    # Проверяем баланс
    score = await score_service.get_score(user_id, chat_id)
    if score.value < bet:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(score.value)
        await reply_and_delete(
            message,
            formatter._t["ttt_not_enough"].format(
                cost=bet, score_word=sw, balance=score.value, score_word_balance=sw_bal,
            ),
        )
        return

    # Списываем ставку с создателя
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=bet,
        emoji=SPECIAL_EMOJI.get("ttt", "🎮"),
    )
    if not result.success:
        sw = p.pluralize(bet)
        sw_bal = p.pluralize(result.current_balance)
        await reply_and_delete(
            message,
            formatter._t["ttt_not_enough"].format(
                cost=bet, score_word=sw, balance=result.current_balance, score_word_balance=sw_bal,
            ),
        )
        return

    # Создаём лобби
    game_id = _make_game_id()
    display = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
    sw_bet = p.pluralize(bet)

    data = {
        "game_id": game_id,
        "state": "lobby",  # lobby → playing → finished
        "player_x": user_id,
        "player_x_name": message.from_user.full_name or "",
        "player_x_username": message.from_user.username or "",
        "player_o": None,
        "player_o_name": "",
        "player_o_username": "",
        "board": [0] * 9,
        "history_x": [],
        "history_o": [],
        "turn": "",  # определится при accept
        "bet": bet,
        "chat_id": chat_id,
        "message_id": 0,
        "created_at": time.time(),
    }

    key = _ttt_key(chat_id, game_id)
    await store._r.set(key, json.dumps(data), ex=_TTT_LOBBY_TTL)

    lobby_text = formatter._t["ttt_lobby"].format(
        user=display, bet=bet, score_word=sw_bet,
    )
    sent = await message.answer(
        lobby_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_lobby_kb(game_id),
    )

    # Сохраняем message_id
    data["message_id"] = sent.message_id
    await store._r.set(key, json.dumps(data), ex=_TTT_LOBBY_TTL)
