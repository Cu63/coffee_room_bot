"""Middleware: штраф IQ за сообщение с подстрокой «67».

За каждое сообщение (в группе/супергруппе), содержащее config.iq.substr_trigger
(«67»), у автора снимается config.iq.substr_penalty IQ. Терять IQ можно
неограниченно, так что штраф применяется всегда, когда триггер найден."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.application.iq_service import IqService
from bot.infrastructure.config_loader import AppConfig

logger = logging.getLogger(__name__)


class IqPenaltyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            await self._maybe_penalize(event, data["dishka_container"])
        return await handler(event, data)

    async def _maybe_penalize(self, message: Message, container: AsyncContainer) -> None:
        # Только группы: в ЛС (создание слова для /word и т.п.) штраф не имеет смысла
        if message.chat.type not in ("group", "supergroup"):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        config = await container.get(AppConfig)
        cfg = config.iq
        if not cfg.enabled or cfg.substr_penalty <= 0 or not cfg.substr_trigger:
            return

        text = message.text or message.caption or ""
        if cfg.substr_trigger not in text:
            return

        iq_service = await container.get(IqService)
        new_iq = await iq_service.add_iq(message.from_user.id, message.chat.id, -cfg.substr_penalty)
        logger.debug(
            "iq: user %d in chat %d −%d IQ за «%s» (итого %d)",
            message.from_user.id, message.chat.id, cfg.substr_penalty, cfg.substr_trigger, new_iq,
        )
