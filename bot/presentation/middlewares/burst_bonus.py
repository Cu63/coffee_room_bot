"""Middleware: бонус за активность (burst) — N сообщений за M минут."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.application.score_service import ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


class BurstBonusMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            await self._maybe_burst(event, data["dishka_container"])
        return await handler(event, data)

    async def _maybe_burst(self, message: Message, container: AsyncContainer) -> None:
        config = await container.get(AppConfig)
        cfg = config.burst

        if not cfg.enabled:
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if message.text and message.text.startswith("/"):
            return
        if message.forward_origin is not None:
            return

        text = message.text or message.caption or ""
        if len(text) < cfg.min_length:
            return

        user_id = message.from_user.id
        chat_id = message.chat.id
        store = await container.get(RedisStore)

        if await store.burst_cooldown_active(user_id, chat_id):
            return

        window_seconds = cfg.window_minutes * 60
        count = await store.burst_add_message(user_id, chat_id, window_seconds)

        if count >= cfg.messages_required:
            score_service = await container.get(ScoreService)
            cooldown_seconds = cfg.cooldown_hours * 3600
            await store.burst_set_cooldown(user_id, chat_id, cooldown_seconds)
            new_value = await score_service.award_burst(user_id, chat_id, cfg.reward)
            logger.debug(
                "burst: user %d in chat %d awarded %d, new score %d",
                user_id, chat_id, cfg.reward, new_value,
            )
