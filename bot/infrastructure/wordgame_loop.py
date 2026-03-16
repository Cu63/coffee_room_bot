"""Фоновая задача: завершение просроченных игр «Угадайка»."""

from __future__ import annotations

import asyncio
import json
import logging
import time

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
                now = time.time()

                async for key in store._r.scan_iter("wg:game:*"):
                    raw = await store._r.get(key)
                    if raw is None:
                        continue
                    data = json.loads(raw)
                    if data.get("finished") or data.get("ends_at", 0) > now:
                        continue

                    finished = await store.wg_game_finish(data["game_id"])
                    if not finished:
                        continue

                    chat_id = finished["chat_id"]
                    word = finished["word"]
                    message_id = finished.get("message_id", 0)

                    logger.info("wordgame: game %s expired in chat %d", finished["game_id"], chat_id)

                    # Редактируем игровое сообщение — показываем слово
                    if message_id:
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=(
                                    f"⏰ <b>Угадайка завершена — время вышло!</b>\n\n"
                                    f"Загаданное слово: <b>{word}</b>\n\n"
                                    f"<i>Никто не угадал.</i>"
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