"""Middleware: удаление сообщений участников под «мутом овнера» (soft-mute)."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


class OwnerMuteDeleteMiddleware(BaseMiddleware):
    """Если отправитель находится под owner-mute — удаляет сообщение и прерывает цепочку.

    Должна быть зарегистрирована как outer-middleware *после* setup_dishka,
    чтобы dishka_container уже был в data.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        if event.from_user is None:
            return await handler(event, data)

        container: AsyncContainer | None = data.get("dishka_container")
        if container is None:
            return await handler(event, data)

        store: RedisStore = await container.get(RedisStore)
        if await store.owner_mute_active(event.chat.id, event.from_user.id):
            try:
                await event.delete()
            except TelegramBadRequest:
                pass
            return None  # прерываем цепочку — хендлеры не вызываются

        return await handler(event, data)