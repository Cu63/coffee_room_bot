"""Фоновая задача: проверка истёкших мутов и восстановление прав."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def unmute_loop(container, bot: Bot, interval_seconds: int) -> None:
    from bot.application.mute_service import MuteService
    from bot.presentation.handlers._admin_utils import _unmute_user

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with container() as scope:
                mute_service = await scope.get(MuteService)
                expired = await mute_service.get_expired_mutes()
                for entry in expired:
                    logger.info(
                        "Unmuting user %d in chat %d (was_admin=%s)",
                        entry.user_id, entry.chat_id, entry.was_admin,
                    )
                    await _unmute_user(bot, mute_service, entry)
        except Exception:
            logger.exception("Unmute task failed")
