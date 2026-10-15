"""/bj <ставка> — создать лобби для дуэльного блекджека."""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.stake_service import StakeService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.blackjack.helpers import (
    _BJ_LOBBY_TTL,
    _BJ_REDIS_BUFFER,
    _bj_key,
    _lobby_kb,
    _make_game_id,
)
from bot.presentation.utils import NO_PREVIEW, check_bet, check_gameban, reply_and_delete

router = Router(name="blackjack_start")


@router.message(Command("bj"))
@inject
async def cmd_blackjack(
    message: Message,
    store: FromDishka[RedisStore],
    stakes: FromDishka[StakeService],
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

    bet, bet_error = check_bet(args[0], bjc.min_bet, bjc.max_bet, formatter)
    if bet_error:
        await reply_and_delete(message, bet_error)
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

    # Списываем ставку в банк игры
    game_id = _make_game_id()
    result = await stakes.hold(user_id, chat_id, f"bj:{game_id}", bet)
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
    display = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
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
        "pot": True,  # ставки в банке игры (game_pots)
        "chat_id": chat_id,
        "message_id": 0,
        "created_at": time.time(),
        "expires_at": time.time() + _BJ_LOBBY_TTL,
    }

    key = _bj_key(chat_id, game_id)
    await store.game_create(key, data, _BJ_LOBBY_TTL + _BJ_REDIS_BUFFER)

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

    # Сохраняем message_id — только пока это лобби: принятую игру не затираем
    await store.game_patch_if_state(key, "lobby", {"message_id": sent.message_id})
