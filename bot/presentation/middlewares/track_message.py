"""Middleware: upsert пользователя, сохранение сообщения, регистрация анон-чата."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.application.interfaces.message_repository import IMessageRepository, MessageInfo
from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.entities import User
from bot.domain.tz import TZ_MSK
from bot.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


class TrackMessageMiddleware(BaseMiddleware):
    """Записывает автора и время каждого входящего сообщения.

    Работает как outer-middleware на Message — вызывается ДО хэндлеров,
    поэтому и команды, и обычные сообщения трекаются.

    bot_me передаётся при инициализации из main() — один раз при старте,
    чтобы не делать лишний запрос к Telegram API.
    """

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
            container: AsyncContainer = data["dishka_container"]

            user_repo = await container.get(IUserRepository)
            message_repo = await container.get(IMessageRepository)

            await user_repo.upsert(
                User(
                    id=event.from_user.id,
                    username=event.from_user.username,
                    full_name=event.from_user.full_name or "",
                    is_bot=event.from_user.is_bot,
                )
            )

            # Сохраняем текст сообщения, но только «живые» реплики:
            # — команды (начинаются с /) не сохраняем
            # — ответы боту не сохраняем (игровые ходы, /help кнопки и т.п.)
            msg_text: str | None = event.text or event.caption or None
            if msg_text and msg_text.startswith("/"):
                msg_text = None
            elif (
                msg_text
                and event.reply_to_message is not None
                and event.reply_to_message.from_user is not None
                and event.reply_to_message.from_user.id == self._bot_me.id
            ):
                msg_text = None

            await message_repo.save(
                MessageInfo(
                    message_id=event.message_id,
                    chat_id=event.chat.id,
                    user_id=event.from_user.id,
                    sent_at=event.date or datetime.now(TZ_MSK),
                    text=msg_text,
                    is_reply=(
                        event.reply_to_message is not None
                        and event.reply_to_message.from_user is not None
                        and not event.reply_to_message.from_user.is_bot
                    ),
                )
            )

            # Авто-регистрация чата для /anon
            if event.chat.type in ("group", "supergroup") and event.chat.title:
                store = await container.get(RedisStore)
                await store.anon_register_chat(event.chat.id, event.chat.title)

        return await handler(event, data)
