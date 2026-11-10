"""Фоновая задача: удаление устаревших событий реакций и сообщений."""

from __future__ import annotations

import asyncio
import logging

from bot.infrastructure.background import loop_pass

logger = logging.getLogger(__name__)


async def cleanup_loop(container, interval_hours: int) -> None:
    from bot.application.cleanup_service import CleanupService

    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            async with container() as scope:
                service = await scope.get(CleanupService)
                await service.delete_expired_events()
                await service.delete_expired_messages()
            loop_pass()
        except Exception:
            logger.exception("Cleanup task failed")
