from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Chat, Message, TelegramObject, Update


class ChatContextMiddleware(BaseMiddleware):
    """Прокидывает chat_id во все хэндлеры.

    Также отсекает события из личных чатов — бот работает только в группах/супергруппах.
    """

    ALLOWED_CHAT_TYPES = {"group", "supergroup"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat: Chat | None = data.get("event_chat")

        if chat is None:
            return None

        if chat.type not in self.ALLOWED_CHAT_TYPES:
            return None

        data["chat_id"] = chat.id
        return await handler(event, data)
