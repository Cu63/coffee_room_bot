"""Middleware: повтор запроса при сетевых ошибках Telegram API."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_DELAYS = (0.5, 2.0)  # пауза перед 2-й и 3-й попыткой (секунды)


class RetryNetworkMiddleware(BaseMiddleware):
    """Повторяет обработку апдейта при TelegramNetworkError.

    До _MAX_ATTEMPTS попыток с экспоненциальными паузами.
    После исчерпания попыток — тихо пропускает апдейт (не роняет бота).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        last_exc: TelegramNetworkError | None = None

        for attempt in range(_MAX_ATTEMPTS):
            try:
                return await handler(event, data)
            except TelegramNetworkError as e:
                last_exc = e
                if attempt < _MAX_ATTEMPTS - 1:
                    delay = _DELAYS[attempt]
                    logger.warning(
                        "TelegramNetworkError (attempt %d/%d), retry in %.1fs: %s",
                        attempt + 1, _MAX_ATTEMPTS, delay, e,
                    )
                    await asyncio.sleep(delay)

        logger.error(
            "TelegramNetworkError after %d attempts, dropping update: %s",
            _MAX_ATTEMPTS, last_exc,
        )
        return None
