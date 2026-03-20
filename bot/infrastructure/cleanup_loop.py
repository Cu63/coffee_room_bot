"""Фоновая задача: удаление устаревших событий."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def cleanup_loop(container, interval_hours: int) -> None:
    from bot.application.cleanup_service import CleanupService

    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            async with container() as scope:
                service = await scope.get(CleanupService)
                await service.delete_expired_events()
        except Exception:
            logger.exception("Cleanup task failed")
