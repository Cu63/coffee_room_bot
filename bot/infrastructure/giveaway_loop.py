"""Фрагмент для вставки в main.py — фоновая задача завершения розыгрышей.

Вставь это рядом с существующими background tasks (cleanup, unmute).
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot

logger = logging.getLogger(__name__)


# В функции main() добавь вызов:
#   asyncio.create_task(giveaway_loop(bot, container))

async def giveaway_loop(bot: Bot, container) -> None:  # type: ignore[type-arg]
    """Каждые 60 секунд проверяет и завершает просроченные розыгрыши."""
    from bot.application.giveaway_service import GiveawayService
    from bot.domain.pluralizer import ScorePluralizer
    from bot.presentation.handlers.giveaway import _post_results

    while True:
        await asyncio.sleep(60)
        try:
            async with container() as scope:
                service: GiveawayService = await scope.get(GiveawayService)
                pluralizer: ScorePluralizer = await scope.get(ScorePluralizer)
                results = await service.finish_expired(datetime.now().astimezone())

            for result in results:
                await _post_results(
                    bot,
                    result.giveaway,
                    result.winners,
                    result.participants_count,
                    pluralizer,
                )
                logger.info(
                    "Auto-finished giveaway %d in chat %d, %d winners",
                    result.giveaway.id,
                    result.giveaway.chat_id,
                    len(result.winners),
                )
        except Exception:
            logger.exception("Error in giveaway_loop")