"""Middleware: автоудаление команд пользователя через заданное время."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import Bot
from aiogram.types import Message, TelegramObject

from bot.presentation.utils import schedule_delete

_COMMAND_DELETE_DELAY = 60  # секунд


class AutoDeleteCommandMiddleware:
    """Удаляет входящее сообщение-команду (/cmd ...) через _COMMAND_DELETE_DELAY секунд.

    Не затрагивает ответы бота — каждый хендлер управляет ими самостоятельно.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        # Планируем удаление только после успешной обработки
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            bot: Bot | None = event.bot or data.get("bot")
            if bot:
                schedule_delete(bot, event, delay=_COMMAND_DELETE_DELAY)
        return result
