"""Фоновая задача: рефунд ставок за истёкшие дуэльные игры блекджека."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def bj_cleanup_loop(container, bot: Bot) -> None:
    """Сканирует bj:duel:* с полем expires_at. Если время вышло:
    - Лобби (state=lobby): возврат ставки создателю
    - Игра (state=playing): возврат ставок обоим игрокам
    """
    from bot.application.interfaces.score_repository import IScoreRepository
    from bot.infrastructure.redis_store import RedisStore

    while True:
        await asyncio.sleep(15)
        try:
            async with container() as scope:
                store = await scope.get(RedisStore)
                score_repo = await scope.get(IScoreRepository)
                for data in await store.bj_duel_pop_expired():
                    state = data.get("state", "")
                    bet = data.get("bet", 0)
                    chat_id = data.get("chat_id", 0)
                    message_id = data.get("message_id", 0)

                    if state == "lobby":
                        p1_id = data.get("p1_id", 0)
                        if bet > 0 and p1_id:
                            await score_repo.add_delta(p1_id, chat_id, bet)
                            logger.info("BJ lobby expired: refunded %d to %d", bet, p1_id)
                        if message_id:
                            try:
                                await bot.edit_message_text(
                                    "⏰ Время вышло. Никто не принял вызов. Ставка возвращена.",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    reply_markup=None,
                                )
                            except Exception:
                                pass
                    elif state == "playing":
                        p1_id = data.get("p1_id", 0)
                        p2_id = data.get("p2_id", 0)
                        if bet > 0:
                            if p1_id:
                                await score_repo.add_delta(p1_id, chat_id, bet)
                            if p2_id:
                                await score_repo.add_delta(p2_id, chat_id, bet)
                            logger.info("BJ game expired: refunded %d each to %d, %d", bet, p1_id, p2_id)
                        if message_id:
                            try:
                                await bot.edit_message_text(
                                    "⏰ Время вышло! Ставки возвращены обоим игрокам.",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    reply_markup=None,
                                )
                            except Exception:
                                pass
        except Exception:
            logger.exception("BJ cleanup loop failed")
