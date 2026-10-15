"""Фоновая задача: рефунд ставок за истёкшие дуэльные приглашения."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.domain.ledger import LedgerKind

logger = logging.getLogger(__name__)


async def duel_cleanup_loop(container, bot: Bot) -> None:
    """Сканирует duel:invite:* с полем expires_at. Если время вышло —
    возвращает ставку создателю и редактирует сообщение.
    """
    from bot.infrastructure.redis_store import RedisStore

    store: RedisStore = await container.get(RedisStore)
    while True:
        await asyncio.sleep(15)
        try:
            async for data in store.duel_invite_iter_expired():
                try:
                    await _refund_expired(container, bot, data)
                except Exception:
                    logger.exception("Duel cleanup: failed to refund invite %s", data.get("invite_id"))
        except Exception:
            logger.exception("Duel cleanup loop failed")


async def _refund_expired(container, bot: Bot, data: dict) -> None:
    from bot.application.stake_service import StakeService

    bet = data.get("bet", 0)
    chat_id = data.get("chat_id", 0)
    challenger_id = data.get("challenger_id", 0)
    message_id = data.get("message_id", 0)

    # Отдельная короткая транзакция на каждое приглашение
    async with container() as scope:
        stakes = await scope.get(StakeService)
        if bet > 0 and challenger_id:
            ref = f"duel:{data.get('invite_id', '')}"
            await stakes.pay(challenger_id, chat_id, ref, bet, kind=LedgerKind.REFUND, pot=data.get("pot", False))
            logger.info("Duel invite expired: refunded %d to challenger %d", bet, challenger_id)

    if message_id:
        try:
            await bot.edit_message_text(
                "⏰ Время вышло. Дуэль не была принята. Ставка возвращена.",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramAPIError as e:
            logger.warning("Duel cleanup: не удалось обновить сообщение %d в чате %d: %s", message_id, chat_id, e)
