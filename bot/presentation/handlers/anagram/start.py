"""Хендлер /anagram — начало игры."""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.infrastructure.word_loader import pick_random_word
from bot.presentation.utils import check_gameban, reply_and_delete, schedule_delete

from bot.presentation.handlers.anagram.helpers import _make_game_id, _shuffle_word, _game_text

router = Router(name="anagram_start")


@router.message(Command("anagram"))
@inject
async def cmd_anagram(
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

    cfg = config.anagram
    if not cfg.enabled:
        await reply_and_delete(message, "❌ Анаграммы отключены.")
        return

    # Проверка кулдауна между играми /anagram
    cd_remaining = await store.anagram_cooldown_active(message.chat.id)
    if cd_remaining is not None:
        mins = cd_remaining // 60
        secs = cd_remaining % 60
        if mins > 0:
            cd_str = f"{mins} мин {secs} сек" if secs else f"{mins} мин"
        else:
            cd_str = f"{secs} сек"
        await reply_and_delete(
            message,
            f"⏳ Подожди ещё <b>{cd_str}</b> перед следующей игрой /anagram.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = (message.text or "").split()[1:]
    p = pluralizer
    chat_id = message.chat.id

    if not args:
        sw = p.pluralize(cfg.max_bet)
        await reply_and_delete(
            message,
            f"🔤 <b>Анаграмма</b>\n\n"
            f"Использование: <code>/anagram &lt;ставка&gt;</code>\n"
            f"Ставка: {cfg.min_bet}–{cfg.max_bet} {sw}\n\n"
            f"Бот загадывает слово из {cfg.min_word_length}–{cfg.max_word_length} букв.\n"
            f"Неверная попытка стоит <b>{cfg.attempt_cost}</b> балл.",
            parse_mode=ParseMode.HTML,
        )
        return

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

    # Проверяем, нет ли уже активной игры в чате
    existing_id = await store.anagram_active_get(chat_id)
    if existing_id:
        if await store.anagram_game_get(existing_id):
            await reply_and_delete(
                message,
                "❌ В этом чате уже есть активная анаграмма. Дождитесь завершения!",
            )
            return
        # Ключ игры протух, но active не почистился — подчищаем
        await store.anagram_active_delete(chat_id)

    # Выбираем слово из словаря
    word = pick_random_word(cfg.min_word_length, cfg.max_word_length)
    if word is None:
        await reply_and_delete(
            message,
            "❌ Не удалось подобрать слово. Попробуй позже.",
        )
        return

    user_id = message.from_user.id

    # Upsert пользователя
    await user_repo.upsert(User(
        id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    ))

    # Проверяем баланс бота и списываем ставку с него
    bot_balance = await score_service.get_bot_balance(message.bot.id, chat_id)
    if bot_balance < bet:
        await reply_and_delete(
            message,
            f"❌ У бота недостаточно баллов для приза ({bot_balance} < {bet}). "
            f"Попроси администратора пополнить баланс.",
        )
        return

    await score_service.add_score(message.bot.id, chat_id, -bet, admin_id=user_id)

    # Создаём игру
    game_id = _make_game_id()
    shuffled = _shuffle_word(word)
    expires_at = time.time() + cfg.answer_timeout_seconds

    data = {
        "game_id": game_id,
        "chat_id": chat_id,
        "creator_id": user_id,
        "word": word,
        "shuffled": shuffled,
        "bet": bet,
        "tries": [],
        "message_id": 0,
        "created_at": time.time(),
        "expires_at": expires_at,
        "is_auto": False,
    }

    sw_bet = p.pluralize(bet)
    game_text = _game_text(shuffled, bet, 0, sw_bet, expires_at)
    sent = await message.answer(game_text, parse_mode=ParseMode.HTML)

    data["message_id"] = sent.message_id
    ttl = cfg.answer_timeout_seconds + 30

    await store.anagram_create_game(game_id, data, chat_id, sent.message_id, ttl)

    # Устанавливаем кулдаун между играми /anagram
    await store.anagram_cooldown_set(chat_id, cfg.cooldown_minutes * 60)

    try:
        await message.bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pass

    # Удаляем исходную команду
    schedule_delete(sent.bot, message, delay=5)
