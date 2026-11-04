"""Фоновая задача: проверка истёкших мутов и восстановление прав."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def _tick(bot: Bot, container) -> None:
    from bot.application.mute_service import MuteService
    from bot.presentation.handlers._admin_utils import lift_mute

    async with container() as scope:
        mute_service = await scope.get(MuteService)
        for entry in await mute_service.get_expired_mutes():
            logger.info(
                "Unmuting user %d in chat %d (was_admin=%s)",
                entry.user_id,
                entry.chat_id,
                entry.was_admin,
            )
            await lift_mute(bot, mute_service, entry)


async def unmute_loop(container, bot: Bot, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await _tick(bot, container)
        except Exception:
            logger.exception("Unmute task failed")
