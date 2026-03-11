from __future__ import annotations

import logging
import random
from datetime import datetime
from bot.domain.tz import TZ_MSK
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, ReactionTypeEmoji
from dishka import AsyncContainer

from bot.domain.entities import User
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.message_repository import IMessageRepository, MessageInfo
from bot.application.score_service import ScoreService
from bot.domain.reaction_registry import ReactionRegistry
from bot.infrastructure.config_loader import AppConfig

logger = logging.getLogger(__name__)


class TrackMessageMiddleware(BaseMiddleware):
    """Записывает автора и время каждого входящего сообщения.
    Опционально ставит случайную реакцию с заданной вероятностью.

    Работает как outer-middleware на Message — вызывается ДО хэндлеров,
    поэтому и команды, и обычные сообщения трекаются.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            container: AsyncContainer = data["dishka_container"]

            user_repo = await container.get(IUserRepository)
            message_repo = await container.get(IMessageRepository)

            await user_repo.upsert(
                User(
                    id=event.from_user.id,
                    username=event.from_user.username,
                    full_name=event.from_user.full_name or "",
                )
            )
            await message_repo.save(
                MessageInfo(
                    message_id=event.message_id,
                    chat_id=event.chat.id,
                    user_id=event.from_user.id,
                    sent_at=event.date or datetime.now(TZ_MSK),
                )
            )

            await self._maybe_react(event, container)

        return await handler(event, data)

    async def _maybe_react(self, message: Message, container: AsyncContainer) -> None:
        config = await container.get(AppConfig)
        cfg = config.auto_react

        if not cfg.enabled:
            return
        if message.bot is None or message.from_user is None:
            return
        # Не реагируем на сообщения самого бота
        if message.from_user.id == message.bot.id:
            return
        # Бросаем кубик
        if random.random() >= cfg.probability:
            return

        registry = await container.get(ReactionRegistry)
        reactions = [
            (emoji, r) for emoji, r in registry._reactions.items()
            if not cfg.positive_only or r.weight > 0
        ]
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

        # Засчитываем реакцию вручную — бот не получает события о своих реакциях.
        # Сначала upsert бота в users (иначе FK на score_events упадёт),
        # затем применяем реакцию без лимитов (бот не ограничен).
        user_repo = await container.get(IUserRepository)
        bot_info = await message.bot.get_me()
        await user_repo.upsert(User(
            id=message.bot.id,
            username=bot_info.username,
            full_name=bot_info.full_name,
        ))

        score_service = await container.get(ScoreService)
        result = await score_service.apply_reaction_no_limits(
            actor_id=message.bot.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            emoji=emoji,
        )
        logger.debug(
            "auto_react: %s on msg %d — applied=%s",
            emoji, message.message_id, result.applied,
        )