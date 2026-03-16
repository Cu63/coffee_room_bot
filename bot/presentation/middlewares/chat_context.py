from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Chat, TelegramObject


class ChatContextMiddleware(BaseMiddleware):
    """Прокидывает chat_id во все хэндлеры.

    Для групп/супергрупп — устанавливает chat_id и пропускает дальше.
    Для личных чатов — пропускает без chat_id (нужно для приёма слова в Угадайке).
    Остальные типы чатов (channels и т.д.) — отсекает.
    """

    GROUP_CHAT_TYPES = {"group", "supergroup"}
    ALLOWED_CHAT_TYPES = {"group", "supergroup", "private"}

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

        if chat.type in self.GROUP_CHAT_TYPES:
            data["chat_id"] = chat.id

        return await handler(event, data)