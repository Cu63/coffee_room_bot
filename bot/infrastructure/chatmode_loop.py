"""Фоновая задача: снятие истёкших режимов чата."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def chatmode_loop(bot: Bot, container) -> None:
    """Каждые 10 секунд проверяет истёкшие chatmode и восстанавливает права."""
    while True:
        await asyncio.sleep(10)
        try:
            async with container() as scope:
                from bot.application.chatmode_service import ChatmodeService
                service: ChatmodeService = await scope.get(ChatmodeService)
                expired = await service.get_expired()
                for entry in expired:
                    logger.info(
                        "chatmode_loop: restoring chat %d (mode=%s, expired)",
                        entry.chat_id, entry.mode,
                    )
                    await service.deactivate(bot, entry)
        except Exception:
            logger.exception("chatmode_loop: error")
