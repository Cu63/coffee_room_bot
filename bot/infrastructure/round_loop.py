"""Фоновая задача: раунды /quiz и /whosaid, на которые не ответили вовремя, — показать верный ответ."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)

_INTERVAL = 5


async def _tick(bot: Bot, container) -> None:
    from bot.infrastructure.message_formatter import MessageFormatter
    from bot.infrastructure.redis_store import RedisStore
    from bot.presentation.handlers.rounds.common import finish_expired

    async with container() as scope:
        store = await scope.get(RedisStore)
        formatter = await scope.get(MessageFormatter)
        async for data in store.round_iter_expired():
            await store.round_release_chat(data["chat_id"], data["id"])
            await finish_expired(bot, formatter, data)


async def round_loop(bot: Bot, container) -> None:
    while True:
        await asyncio.sleep(_INTERVAL)
        try:
            await _tick(bot, container)
        except Exception:
            logger.exception("round loop failed")
