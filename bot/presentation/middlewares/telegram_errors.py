"""Middleware: обработка ожидаемых ошибок Telegram API на внешнем уровне."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import TelegramObject

from bot.presentation.errors import AbortUpdate

# Ошибки callback-запросов, которые не нужно логировать — это норма
_IGNORED_BAD_REQUEST = (
    "query is too old",
    "message to be replied not found",
    "message can't be deleted",
    "message to delete not found",
    "message is not modified",
)

logger = logging.getLogger(__name__)


class TelegramErrorsMiddleware(BaseMiddleware):
    """Гасит ожидаемые ошибки Telegram и AbortUpdate, не роняя обработку апдейтов.

    Хендлер здесь НЕ перезапускается: транзакция запроса к этому моменту
    уже закрыта, и повторный запуск провёл бы операцию (перевод, покупку)
    второй раз. Сетевые сбои повторяются на уровне отдельных запросов
    к Bot API — см. RetryIdempotentRequestsMiddleware.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except AbortUpdate:
            # Хендлер сам ответил пользователю; транзакция уже откачена
            return None
        except TelegramBadRequest as e:
            # Ожидаемые ошибки — тихо игнорируем
            if any(msg in str(e) for msg in _IGNORED_BAD_REQUEST):
                logger.debug("Ignored TelegramBadRequest: %s", e)
                return None
            raise
        except TelegramNetworkError as e:
            logger.warning("TelegramNetworkError, dropping update: %s", e)
            return None
