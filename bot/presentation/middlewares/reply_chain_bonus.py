"""Middleware: бонус за чередующийся диалог через реплаи (reply chain)."""

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


class ReplyChainMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            await self._maybe_reply_chain(event, data["dishka_container"])
        return await handler(event, data)

    async def _maybe_reply_chain(self, message: Message, container: AsyncContainer) -> None:
        config = await container.get(AppConfig)
        cfg = config.reply_chain

        if not cfg.enabled:
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if message.reply_to_message is None:
            return
        if message.reply_to_message.from_user is None:
            return

        replier_id = message.from_user.id
        author_id = message.reply_to_message.from_user.id

        if replier_id == author_id:
            return
        if message.reply_to_message.from_user.is_bot:
            return

        chat_id = message.chat.id
        store = await container.get(RedisStore)

        if await store.chain_cooldown_active(chat_id, replier_id, author_id):
            return

        window_seconds = cfg.window_minutes * 60
        count = await store.chain_add_reply(chat_id, replier_id, author_id, window_seconds)

        if count is not None and count >= cfg.replies_required:
            score_service = await container.get(ScoreService)
            cooldown_seconds = cfg.cooldown_hours * 3600
            await store.chain_award_cleanup(chat_id, replier_id, author_id, cooldown_seconds)
            for uid in (replier_id, author_id):
                new_value = await score_service.award_chain(uid, chat_id, cfg.reward)
                logger.debug(
                    "chain: user %d in chat %d awarded %d, new score %d",
                    uid, chat_id, cfg.reward, new_value,
                )
