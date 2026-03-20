"""Фоновая задача анаграммы.

Два тика:
  1. expire_tick  (каждые 10 сек) — завершает просроченные игры, показывает слово.
  2. auto_tick    (каждую минуту) — публикует новые игры в зарегистрированных чатах
                                    согласно настройке games_per_hour.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

_RESULT_DELETE_DELAY = 60   # секунд до удаления итогового сообщения


async def anagram_expire_loop(bot: Bot, container) -> None:
    """Каждые 10 секунд финишит просроченные игры."""
    while True:
        await asyncio.sleep(10)
        try:
            await _expire_tick(bot, container)
        except Exception:
            logger.exception("anagram_expire_loop: неожиданная ошибка")


async def anagram_auto_loop(bot: Bot, container) -> None:
    """Каждую минуту проверяет, не пора ли запустить авто-игру в каком-нибудь чате."""
    while True:
        await asyncio.sleep(60)
        try:
            await _auto_tick(bot, container)
        except Exception:
            logger.exception("anagram_auto_loop: неожиданная ошибка")


# ── expire tick ─────────────────────────────────────────────────────────

async def _expire_tick(bot: Bot, container) -> None:
    from bot.application.score_service import ScoreService
    from bot.infrastructure.redis_store import RedisStore
    from bot.presentation.utils import schedule_delete_id

    async with container() as scope:
        store: RedisStore = await scope.get(RedisStore)
        score_service: ScoreService = await scope.get(ScoreService)

        for data in await store.anagram_scan_expired():
            game_id  = data["game_id"]
            chat_id  = data["chat_id"]
            word     = data["word"]
            bet      = data.get("bet", 0)
            msg_id   = data.get("message_id", 0)

            # Возвращаем деньги боту (если никто не угадал, банк вернуть)
            if bet > 0:
                await score_service.add_score(bot.id, chat_id, bet, admin_id=bot.id)

            logger.info(
                "anagram_expire: игра %s в чате %d истекла (слово: %s)",
                game_id, chat_id, word,
            )

            if msg_id:
                try:
                    await bot.unpin_chat_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=(
                            f"⏰ <b>Анаграмма завершена — время вышло!</b>\n\n"
                            f"Загаданное слово: <b>{word}</b>\n\n"
                            f"<i>Никто не угадал.</i>"
                        ),
                        parse_mode="HTML",
                    )
                    schedule_delete_id(bot, chat_id, msg_id, delay=_RESULT_DELETE_DELAY)
                except TelegramBadRequest:
                    pass
                except Exception:
                    logger.exception("anagram_expire: не удалось отредактировать сообщение")


# ── auto tick ────────────────────────────────────────────────────────────

async def _auto_tick(bot: Bot, container) -> None:
    from bot.application.score_service import ScoreService
    from bot.domain.pluralizer import ScorePluralizer
    from bot.infrastructure.config_loader import AppConfig
    from bot.infrastructure.redis_store import RedisStore
    from bot.presentation.handlers.anagram import create_anagram_game

    async with container() as scope:
        cfg: AppConfig = await scope.get(AppConfig)
        acfg = cfg.anagram

        if not acfg.enabled or acfg.games_per_hour <= 0:
            return

        store: RedisStore = await scope.get(RedisStore)
        score_service: ScoreService = await scope.get(ScoreService)
        pluralizer: ScorePluralizer = await scope.get(ScorePluralizer)
        now = time.time()
        interval = 3600.0 / acfg.games_per_hour  # секунд между играми

        # Берём все зарегистрированные чаты
        chat_entries = await store.anagram_chats_all()

        for chat_id, last_ts in chat_entries:
            # Проверяем время следующей авто-игры
            next_ts = await store.anagram_next_auto_get(chat_id)
            if next_ts is None:
                next_ts = last_ts + interval

            if now < next_ts:
                continue

            # Запускаем игру
            bet = acfg.auto_bet
            success = await create_anagram_game(
                bot=bot,
                chat_id=chat_id,
                bet=bet,
                store=store,
                score_service=score_service,
                pluralizer=pluralizer,
                cfg=acfg,
            )

            # Планируем следующую авто-игру в любом случае (чтобы не спамить)
            next_auto = now + interval
            await store.anagram_next_auto_set(chat_id, next_auto, int(interval * 3))

            if success:
                logger.info(
                    "anagram_auto_tick: опубликована авто-игра в чате %d (следующая через %.0f сек)",
                    chat_id, interval,
                )
