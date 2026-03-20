"""Фоновая задача: завершение просроченных игр «Угадайка»."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


async def wordgame_loop(bot: Bot, container) -> None:
    """Каждые 5 секунд проверяет просроченные игры и завершает их."""
    while True:
        await asyncio.sleep(5)
        try:
            async with container() as scope:
                from bot.infrastructure.redis_store import RedisStore
                store: RedisStore = await scope.get(RedisStore)

                for game_id in await store.wg_scan_expired():
                    finished = await store.wg_game_finish(game_id)
                    if not finished:
                        continue

                    chat_id = finished["chat_id"]
                    word = finished["word"]
                    bet = finished.get("bet", 0)
                    creator_id = finished.get("creator_id", 0)
                    message_id = finished.get("message_id", 0)

                    logger.info("wordgame: game %s expired in chat %d", finished["game_id"], chat_id)

                    # Возвращаем ставку создателю — никто не угадал
                    if bet > 0 and creator_id:
                        try:
                            from bot.application.score_service import ScoreService
                            score_service: ScoreService = await scope.get(ScoreService)
                            await score_service.add_score(creator_id, chat_id, bet, admin_id=creator_id)
                            logger.info(
                                "wordgame: refunded %d to creator %d in chat %d",
                                bet, creator_id, chat_id,
                            )
                        except Exception:
                            logger.exception("wordgame_loop: failed to refund bet")

                    # Редактируем игровое сообщение — показываем слово
                    if message_id:
                        refund_line = f"\n💰 Ставка <b>{bet}</b> возвращена автору." if bet > 0 else ""
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=(
                                    f"⏰ <b>Угадайка завершена — время вышло!</b>\n\n"
                                    f"Загаданное слово: <b>{word}</b>\n\n"
                                    f"<i>Никто не угадал.</i>{refund_line}"
                                ),
                                parse_mode="HTML",
                            )
                            # Удаляем через 30 секунд
                            asyncio.get_running_loop().create_task(
                                _delete_after(bot, chat_id, message_id, delay=30)
                            )
                        except TelegramBadRequest:
                            pass
                        except Exception:
                            logger.exception("wordgame_loop: failed to edit message")

        except Exception:
            logger.exception("wordgame_loop: unexpected error")


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass
