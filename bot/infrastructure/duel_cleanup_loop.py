"""Фоновая задача: рефунд ставок за истёкшие дуэльные приглашения."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def duel_cleanup_loop(container, bot: Bot) -> None:
    """Сканирует duel:invite:* с полем expires_at. Если время вышло —
    возвращает ставку создателю и редактирует сообщение.
    """
    from bot.application.interfaces.score_repository import IScoreRepository
    from bot.infrastructure.redis_store import RedisStore

    while True:
        await asyncio.sleep(15)
        try:
            async with container() as scope:
                store = await scope.get(RedisStore)
                score_repo = await scope.get(IScoreRepository)
                for data in await store.duel_invite_pop_expired():
                    bet = data.get("bet", 0)
                    chat_id = data.get("chat_id", 0)
                    challenger_id = data.get("challenger_id", 0)
                    message_id = data.get("message_id", 0)

                    if bet > 0 and challenger_id:
                        await score_repo.add_delta(challenger_id, chat_id, bet)
                        logger.info(
                            "Duel invite expired: refunded %d to challenger %d",
                            bet, challenger_id,
                        )
                    if message_id:
                        try:
                            await bot.edit_message_text(
                                "⏰ Время вышло. Дуэль не была принята. Ставка возвращена.",
                                chat_id=chat_id,
                                message_id=message_id,
                                reply_markup=None,
                            )
                        except Exception:
                            pass
        except Exception:
            logger.exception("Duel cleanup loop failed")
