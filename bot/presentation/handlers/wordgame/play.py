"""Обработчики ЛС: /start и приём загаданного слова."""

from __future__ import annotations

import time

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.domain.pluralizer import ScorePluralizer
from bot.domain.wordgame_entities import WordGame, is_valid_word, normalize_word
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.anon import NoAnonState
from bot.presentation.handlers.wordgame.helpers import game_text

router = Router(name="wordgame_play")


# ── /start в ЛС ─────────────────────────────────────────────────────────────

@router.message(Command("start"), F.chat.type == "private")
@inject
async def cmd_start_private(
    message: Message,
    store: FromDishka[RedisStore],
) -> None:
    user_id = message.from_user.id
    game_id = await store.wg_awaiting_get(user_id)
    if game_id is None:
        await message.answer(
            "Привет! Чтобы начать игру, используй /word в групповом чате."
        )
        return

    pending = await store.wg_pending_get(game_id)
    if pending is None:
        await store.wg_awaiting_delete(user_id)
        await message.answer(
            "❌ Игра устарела (прошло больше 10 мин).\n"
            "Начни новую командой /word в группе."
        )
        return

    await message.answer(
        "✏️ Отправь мне загадываемое слово:\n\n"
        "• Только буквы (кириллица или латиница)\n"
        "• Без пробелов и цифр\n"
        "• Длина: не менее 2 символов"
    )


# ── Приём слова в ЛС ─────────────────────────────────────────────────────────

@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), NoAnonState())
@inject
async def msg_private_word(
    message: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    user_id = message.from_user.id
    game_id = await store.wg_awaiting_get(user_id)
    if game_id is None:
        await message.answer(
            "Нет активной игры, ожидающей слово.\n"
            "Начни командой /word в групповом чате."
        )
        return

    word = normalize_word(message.text or "")
    wg = config.wordgame

    if not is_valid_word(word, wg.min_word_length, wg.max_word_length):
        await message.answer(
            f"❌ Неверное слово. Только буквы, длина {wg.min_word_length}–{wg.max_word_length}.\n"
            "Попробуй ещё раз:"
        )
        return

    pending = await store.wg_pending_get(game_id)
    if pending is None:
        await store.wg_awaiting_delete(user_id)
        await message.answer(
            "❌ Игра устарела. Начни новую командой /word в группе."
        )
        return

    await store.wg_pending_delete(game_id)
    await store.wg_awaiting_delete(user_id)

    chat_id = pending["chat_id"]
    bet = pending["bet"]
    duration_secs = pending["duration_seconds"]
    lobby_msg_id = pending.get("lobby_msg_id", 0)
    ends_at = time.time() + duration_secs

    game = WordGame(
        game_id=game_id,
        chat_id=chat_id,
        creator_id=user_id,
        word=word,
        bet=bet,
        ends_at=ends_at,
        message_id=lobby_msg_id,
    )

    await store.wg_game_create(game)
    await store.wg_chat_add(chat_id, game_id)

    if lobby_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=lobby_msg_id,
                text=game_text(game, pluralizer),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            await bot.pin_chat_message(chat_id=chat_id,
                                       message_id = lobby_msg_id,
                                       disable_notification=True)
        except TelegramBadRequest:
            pass

    await message.answer(
        f"✅ Слово принято! Игра запущена.\n"
        f"Игрокам нужно угадать слово из <b>{len(word)}</b> букв.",
        parse_mode=ParseMode.HTML,
    )
