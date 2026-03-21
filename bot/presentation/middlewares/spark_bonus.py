"""Middleware: бонус зачинщику разговора (spark) — N уникальных респондентов."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.application.score_service import ScoreService
from bot.application.xp_service import XpService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


class SparkBonusMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            await self._maybe_spark(event, data["dishka_container"])
        return await handler(event, data)

    async def _maybe_spark(self, message: Message, container: AsyncContainer) -> None:
        config = await container.get(AppConfig)
        cfg = config.spark

        if not cfg.enabled:
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if message.text and message.text.startswith("/"):
            return
        if message.forward_origin is not None:
            return

        user_id = message.from_user.id
        chat_id = message.chat.id
        store = await container.get(RedisStore)
        window_seconds = cfg.window_minutes * 60

        if not await store.spark_cooldown_active(user_id, chat_id):
            active = await store.spark_get_active(chat_id)
            if user_id not in active:
                await store.spark_activate(user_id, chat_id, window_seconds)

        active = await store.spark_get_active(chat_id)
        score_service = await container.get(ScoreService)
        cooldown_seconds = cfg.cooldown_hours * 3600

        for anchor_id in active:
            if anchor_id == user_id:
                continue
            count = await store.spark_add_responder(anchor_id, user_id, chat_id)
            if count >= cfg.unique_responders:
                await store.spark_award_cleanup(anchor_id, chat_id, cooldown_seconds)
                new_value = await score_service.award_spark(anchor_id, chat_id, cfg.reward)
                logger.debug(
                    "spark: user %d in chat %d awarded %d, new score %d",
                    anchor_id, chat_id, cfg.reward, new_value,
                )

                # Начисляем XP зачинщику
                xp_cfg = config.xp
                if xp_cfg.enabled and xp_cfg.rewards.spark > 0:
                    xp_service = await container.get(XpService)
                    result = await xp_service.add_xp(anchor_id, chat_id, xp_cfg.rewards.spark)
                    logger.debug(
                        "spark xp: user %d in chat %d +%d xp (total %d, level %d)",
                        anchor_id, chat_id, xp_cfg.rewards.spark, result.new_xp, result.new_level,
                    )
