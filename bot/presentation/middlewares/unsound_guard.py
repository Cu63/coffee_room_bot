"""Middleware: удаляет сообщения, где тегают участника с активным /unsound.

Проверка идёт по entities сообщения, поэтому в 99% случаев (сообщение без
собачек и без text_mention) обходится вообще без обращений к Redis.
Сам бот под запрет не попадает — ему тегать можно.
"""

from __future__ import annotations

import logging
import time
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.types import Message, TelegramObject
from aiogram.types import User as TgUser
from dishka import AsyncContainer

from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.bot_utils import format_duration
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import schedule_delete

logger = logging.getLogger(__name__)


class UnsoundGuardMiddleware(BaseMiddleware):
    def __init__(self, bot_me: TgUser) -> None:
        self._bot_id = bot_me.id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            try:
                if await self._guard(event, data["dishka_container"]):
                    return None  # сообщение удалено — дальше по цепочке не пускаем
            except Exception:
                logger.exception("unsound_guard: проверка упоминаний упала")
        return await handler(event, data)

    async def _guard(self, message: Message, container: AsyncContainer) -> bool:
        """Возвращает True, если сообщение было удалено."""
        if message.from_user is None:
            return False
        if message.chat.type not in ("group", "supergroup"):
            return False
        # Нашему боту тегать можно
        if message.from_user.id == self._bot_id:
            return False

        entities = message.entities or message.caption_entities or []
        if not entities:
            return False

        config = await container.get(AppConfig)
        if not config.unsound.enabled:
            return False

        store = await container.get(RedisStore)
        chat_id = message.chat.id
        author_id = message.from_user.id
        text = message.text or message.caption or ""

        # ── Кого из упомянутых сейчас нельзя тегать ──────────────────
        targets: dict[int, float] = {}
        for entity in entities:
            user_id: int | None = None
            until: float | None = None

            if entity.type == "mention":
                username = text[entity.offset : entity.offset + entity.length].lstrip("@")
                if not username:
                    continue
                found = await store.unsound_id_by_username(chat_id, username)
                if found is not None:
                    user_id = found
                    until = await store.unsound_until(chat_id, found)
            elif entity.type == "text_mention" and entity.user is not None:
                user_id = entity.user.id
                until = await store.unsound_until(chat_id, user_id)

            # Себя тегать не запрещаем
            if user_id is None or until is None or user_id == author_id:
                continue
            targets[user_id] = until

        if not targets:
            return False

        deleted = False
        try:
            await message.delete()
            deleted = True
        except Exception as e:
            logger.warning("unsound_guard: не удалось удалить сообщение: %s", e)

        await self._notify(message, container, store, config, targets)
        return deleted

    async def _notify(
        self,
        message: Message,
        container: AsyncContainer,
        store: RedisStore,
        config: AppConfig,
        targets: dict[int, float],
    ) -> None:
        """Пишет в чат, что участник недоступен. Уведомление самоудаляется."""
        cfg = config.unsound
        formatter = await container.get(MessageFormatter)
        user_repo = await container.get(IUserRepository)
        now = time.time()

        for user_id, until in targets.items():
            allowed = await store.unsound_notice_allowed(
                chat_id=message.chat.id,
                user_id=user_id,
                cooldown=cfg.notice_cooldown_seconds,
            )
            if not allowed:
                continue

            user = await user_repo.get_by_id(user_id)
            # Имя в <code>: так Telegram не превращает его в живой тег
            name = f"@{user.username}" if user and user.username else (user.full_name if user else "участник")

            try:
                notice = await message.answer(
                    formatter._t["unsound_notice"].format(
                        user=escape(name),
                        remaining=format_duration(max(int(until - now), 0)),
                    ),
                    parse_mode=ParseMode.HTML,
                )
                if message.bot is not None:
                    schedule_delete(message.bot, notice, delay=cfg.notice_delete_seconds)
            except Exception as e:
                logger.debug("unsound_guard: не удалось отправить уведомление: %s", e)
