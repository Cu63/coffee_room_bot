"""Middleware: у пользователя одновременно идёт не больше одного запроса к LLM."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import reply_and_delete

# Страховка на случай падения процесса посреди запроса: так долго LLM-команды не идут
_BUSY_TTL = 600


class OneLlmRequestPerUserMiddleware(BaseMiddleware):
    """Пока у пользователя идёт LLM-команда, следующие отклоняются.

    Без этого дневные лимиты обходились параллельными запросами: каждый проверял
    счётчик раньше, чем предыдущие успевали записаться. Вешается на роутеры
    LLM-команд как inner-middleware, поэтому срабатывает, только когда команда совпала.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)
        container = data["dishka_container"]
        store = await container.get(RedisStore)
        token = await store.llm_busy_acquire(event.from_user.id, _BUSY_TTL)
        if token is None:
            formatter = await container.get(MessageFormatter)
            await reply_and_delete(event, formatter._t["llm_busy"])
            return None
        try:
            return await handler(event, data)
        finally:
            await store.llm_busy_release(event.from_user.id, token)
