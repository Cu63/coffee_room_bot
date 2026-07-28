"""Middleware: удаление сообщений участников под «мутом овнера» (soft-mute)."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)

# Админские команды, которые овнер может выполнять даже под мутом
_OWNER_ALLOWED_COMMANDS = frozenset({
    "add", "sub", "set", "reset",
    "op", "deop", "save", "restore",
    "amute", "aunmute",
    "giveaway", "giveaway_end", "giveaway_period", "giveaway_period_stop",
    "mutegiveaway", "mutegiveaway_end",
    "summary_admin",
    "lot",
})


def _is_admin_command(message: Message) -> bool:
    """Проверяет, является ли сообщение админской командой."""
    if not message.text or not message.text.startswith("/"):
        return False
    cmd = message.text.split()[0].lstrip("/").split("@")[0].lower()
    return cmd in _OWNER_ALLOWED_COMMANDS


class OwnerMuteDeleteMiddleware(BaseMiddleware):
    """Если отправитель находится под owner-mute — удаляет сообщение и прерывает цепочку.

    Исключение: овнер чата может использовать админские команды даже под мутом.

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
            if _is_admin_command(event):
                return await handler(event, data)
            try:
                await event.delete()
            except TelegramBadRequest:
                pass
            return None  # прерываем цепочку — хендлеры не вызываются

        return await handler(event, data)
