from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.domain.entities import User
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.message_repository import IMessageRepository, MessageInfo


class TrackMessageMiddleware(BaseMiddleware):
    """Записывает автора и время каждого входящего сообщения.

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
                    sent_at=event.date or datetime.now(timezone.utc),
                )
            )

        return await handler(event, data)
