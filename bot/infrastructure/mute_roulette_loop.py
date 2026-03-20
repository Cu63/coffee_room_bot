"""Фоновая задача: завершение истёкших мут-рулеток."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def mute_roulette_loop(container, bot: Bot) -> None:
    from bot.application.mute_service import MuteService
    from bot.infrastructure.redis_store import RedisStore
    from bot.presentation.handlers.giveaway import _finish_mute_roulette

    while True:
        await asyncio.sleep(10)
        try:
            async with container() as scope:
                store = await scope.get(RedisStore)
                mute_service = await scope.get(MuteService)
                for chat_id, roulette_id, finished in await store.mute_roulette_pop_expired():
                    logger.info("Auto-finishing mutegiveaway %s in chat %d", roulette_id, chat_id)
                    await _finish_mute_roulette(bot, chat_id, finished, mute_service)
        except Exception:
            logger.exception("Mute roulette loop failed")
