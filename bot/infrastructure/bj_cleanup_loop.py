"""Фоновая задача: истёкшие дуэли в блекджек."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from bot.domain.ledger import LedgerKind

logger = logging.getLogger(__name__)


async def bj_cleanup_loop(container, bot: Bot) -> None:
    """Сканирует bj:duel:* с полем expires_at. Если время вышло:
    - Лобби (state=lobby): ставка возвращается создателю
    - Игра (state=playing): игра не доиграна — обе ставки забирает бот
    """
    from bot.infrastructure.redis_store import RedisStore

    store: RedisStore = await container.get(RedisStore)
    while True:
        await asyncio.sleep(15)
        try:
            async for data in store.bj_duel_iter_expired():
                try:
                    await _settle_expired(container, bot, data)
                except Exception:
                    logger.exception("BJ cleanup: failed to settle game %s", data.get("game_id"))
        except Exception:
            logger.exception("BJ cleanup loop failed")


async def _settle_expired(container, bot: Bot, data: dict) -> None:
    from bot.application.interfaces.score_repository import IScoreRepository

    state = data.get("state", "")
    bet = data.get("bet", 0)
    chat_id = data.get("chat_id", 0)
    message_id = data.get("message_id", 0)

    # У игры из /duel ставки списаны с меткой приглашения
    ref = data.get("ref") or f"bj:{data.get('game_id', '')}"
    text = None
    # Отдельная короткая транзакция на каждую игру: ошибка одной не откатывает остальные
    async with container() as scope:
        score_repo = await scope.get(IScoreRepository)
        if state == "lobby":
            p1_id = data.get("p1_id", 0)
            if bet > 0 and p1_id:
                await score_repo.add_delta(p1_id, chat_id, bet, kind=LedgerKind.REFUND, ref=ref)
                logger.info("BJ lobby expired: refunded %d to %d", bet, p1_id)
            text = "⏰ Время вышло. Никто не принял вызов. Ставка возвращена."
        elif state == "playing":
            if bet > 0:
                await score_repo.add_delta(bot.id, chat_id, bet * 2, kind=LedgerKind.PRIZE, ref=ref)
                logger.info(
                    "BJ game expired: both bets (%d each) of %s and %s go to the bot",
                    bet,
                    data.get("p1_id"),
                    data.get("p2_id"),
                )
            text = "⏰ Время вышло! Игра не доиграна — обе ставки забирает бот."

    if message_id and text:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass
