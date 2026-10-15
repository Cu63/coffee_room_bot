"""Повтор идемпотентных запросов к Bot API при сетевых ошибках."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetUpdates
from aiogram.methods.base import TelegramType

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.methods import Response, TelegramMethod

logger = logging.getLogger(__name__)

# Повтор такого запроса может создать дубль: по таймауту ответ потерялся,
# а сообщение (кубик, ссылка…) уже создано на стороне Telegram.
_NON_IDEMPOTENT_PREFIXES = ("Send", "Forward", "Copy", "Create")
_DELAYS = (0.5, 2.0)  # пауза перед 2-й и 3-й попыткой (секунды)


class RetryIdempotentRequestsMiddleware(BaseRequestMiddleware):
    """Повторяет запрос при TelegramNetworkError, если повтор безопасен.

    Повторяются только идемпотентные методы (edit, delete, restrict, get…).
    Ретрай на уровне отдельного запроса, а не всего апдейта: перезапуск
    хендлера повторил бы и списания баллов.
    """

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        name = type(method).__name__
        # GetUpdates повторяет сам цикл polling
        if isinstance(method, GetUpdates) or name.startswith(_NON_IDEMPOTENT_PREFIXES):
            return await make_request(bot, method)

        for attempt, delay in enumerate(_DELAYS, start=1):
            try:
                return await make_request(bot, method)
            except TelegramNetworkError as e:
                logger.warning(
                    "TelegramNetworkError in %s (attempt %d/%d), retry in %.1fs: %s",
                    name,
                    attempt,
                    len(_DELAYS) + 1,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
        return await make_request(bot, method)
