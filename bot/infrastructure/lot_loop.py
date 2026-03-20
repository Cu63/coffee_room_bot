"""Фоновая задача: завершает просроченные аукционы."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def lot_loop(bot: Bot, container) -> None:
    """Каждые 5 секунд проверяет и завершает просроченные лоты."""
    while True:
        await asyncio.sleep(5)
        try:
            await _tick(bot, container)
        except Exception:
            logger.exception("lot_loop: неожиданная ошибка")


async def _tick(bot: Bot, container) -> None:
    from bot.domain.pluralizer import ScorePluralizer
    from bot.infrastructure.config_loader import AppConfig
    from bot.infrastructure.redis_store import RedisStore
    from bot.presentation.handlers.lot import finish_lot

    async with container() as scope:
        store: RedisStore = await scope.get(RedisStore)
        p: ScorePluralizer = await scope.get(ScorePluralizer)
        cfg: AppConfig = await scope.get(AppConfig)

        for data in await store.lot_scan_expired():
            chat_id = data["chat_id"]
            lot_id = data["lot_id"]
            logger.info("lot_loop: завершаем лот %s в чате %d", lot_id, chat_id)

            await finish_lot(
                bot=bot,
                chat_id=chat_id,
                lot_id=lot_id,
                store=store,
                p=p,
                delete_delay=cfg.lot.delete_delay,
            )
