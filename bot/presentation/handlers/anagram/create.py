"""Публичная утилита для создания анаграммы (используется loop'ом)."""

from __future__ import annotations

import logging
import time

from aiogram.exceptions import TelegramBadRequest

from bot.application.score_service import ScoreService
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.redis_store import RedisStore
from bot.infrastructure.word_loader import pick_random_word

from bot.presentation.handlers.anagram.helpers import _make_game_id, _shuffle_word, _game_text

logger = logging.getLogger(__name__)


async def create_anagram_game(
    bot,
    chat_id: int,
    bet: int,
    store: RedisStore,
    score_service: ScoreService,
    pluralizer: ScorePluralizer,
    cfg,
) -> bool:
    """Создать и опубликовать анаграмму в указанном чате.

    Возвращает True если успешно, False если не удалось (нет баланса, нет слова, уже активна).
    """
    # Проверяем активную игру
    existing_id = await store.anagram_active_get(chat_id)
    if existing_id:
        if await store.anagram_game_get(existing_id):
            return False
        await store.anagram_active_delete(chat_id)

    word = pick_random_word(cfg.min_word_length, cfg.max_word_length)
    if word is None:
        logger.warning("anagram_loop: не удалось подобрать слово для чата %d", chat_id)
        return False

    bot_balance = await score_service.get_bot_balance(bot.id, chat_id)
    if bot_balance < bet:
        logger.warning("anagram_loop: у бота нет баллов в чате %d (%d < %d)", chat_id, bot_balance, bet)
        return False

    await score_service.add_score(bot.id, chat_id, -bet, admin_id=bot.id)

    game_id = _make_game_id()
    shuffled = _shuffle_word(word)
    expires_at = time.time() + cfg.answer_timeout_seconds
    sw_bet = pluralizer.pluralize(bet)
    game_text = _game_text(shuffled, bet, 0, sw_bet, expires_at)

    try:
        sent = await bot.send_message(chat_id, game_text, parse_mode="HTML")
    except Exception as e:
        # Не смогли отправить — возвращаем деньги боту
        await score_service.add_score(bot.id, chat_id, bet, admin_id=bot.id)
        logger.error("anagram_loop: не удалось отправить сообщение в чат %d: %s", chat_id, e)
        return False

    data = {
        "game_id": game_id,
        "chat_id": chat_id,
        "creator_id": bot.id,
        "word": word,
        "shuffled": shuffled,
        "bet": bet,
        "tries": [],
        "message_id": sent.message_id,
        "created_at": time.time(),
        "expires_at": expires_at,
        "is_auto": True,
    }

    ttl = cfg.answer_timeout_seconds + 30
    await store.anagram_create_game(game_id, data, chat_id, sent.message_id, ttl)

    try:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pass

    logger.info(
        "anagram_loop: создана авто-игра %s в чате %d, слово длиной %d, ставка %d",
        game_id, chat_id, len(word), bet,
    )
    return True
