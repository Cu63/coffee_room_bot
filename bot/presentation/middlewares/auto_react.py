"""Middleware: случайная авто-реакция бота на сообщения."""

from __future__ import annotations

import logging
import random
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, ReactionTypeEmoji, TelegramObject
from dishka import AsyncContainer

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.reaction_registry import ReactionRegistry
from bot.infrastructure.config_loader import AppConfig

logger = logging.getLogger(__name__)


class AutoReactMiddleware(BaseMiddleware):
    """С заданной вероятностью ставит случайную реакцию от имени бота."""

    def __init__(self, bot_me) -> None:
        super().__init__()
        self._bot_me = bot_me

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            await self._maybe_react(event, data["dishka_container"])
        return await handler(event, data)

    async def _maybe_react(self, message: Message, container: AsyncContainer) -> None:
        config = await container.get(AppConfig)
        cfg = config.auto_react

        if not cfg.enabled:
            return
        if message.bot is None or message.from_user is None:
            return
        if message.from_user.id == self._bot_me.id:
            return
        if random.random() >= cfg.probability:
            return

        registry = await container.get(ReactionRegistry)
        reactions = [(emoji, r) for emoji, r in registry._reactions.items() if not cfg.positive_only or r.weight > 0]
        if not reactions:
            return

        emoji, _ = random.choice(reactions)

        try:
            await message.bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(type="emoji", emoji=emoji)],
            )
        except Exception as e:
            logger.debug("auto_react: failed to set reaction: %s", e)
            return

        user_repo = await container.get(IUserRepository)
        await user_repo.upsert(
            User(
                id=self._bot_me.id,
                username=self._bot_me.username,
                full_name=self._bot_me.full_name,
                is_bot=True,
            )
        )

        score_service = await container.get(ScoreService)
        result = await score_service.apply_reaction_no_limits(
            actor_id=self._bot_me.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            emoji=emoji,
        )
        logger.debug(
            "auto_react: %s on msg %d — applied=%s",
            emoji, message.message_id, result.applied,
        )
