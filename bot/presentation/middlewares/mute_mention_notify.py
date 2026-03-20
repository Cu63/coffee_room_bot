"""Middleware: уведомление при @mention или reply на замьюченного пользователя."""

from __future__ import annotations

import logging
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.types import Message, TelegramObject
from dishka import AsyncContainer

from bot.application.interfaces.mute_repository import IMuteRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.presentation.utils import schedule_delete

logger = logging.getLogger(__name__)


class MuteMentionNotifyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            await self._maybe_notify_muted(event, data["dishka_container"])
        return await handler(event, data)

    async def _maybe_notify_muted(self, message: Message, container: AsyncContainer) -> None:
        if message.bot is None or message.from_user is None:
            return
        if message.chat.type not in ("group", "supergroup"):
            return
        if message.from_user.is_bot:
            return

        chat_id = message.chat.id
        mute_repo = await container.get(IMuteRepository)

        muted_user_ids: set[int] = set()

        # 1. Reply на чьё-то сообщение
        if (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and not message.reply_to_message.from_user.is_bot
            and message.reply_to_message.from_user.id != message.from_user.id
        ):
            muted_user_ids.add(message.reply_to_message.from_user.id)

        # 2. @mention через entities
        entities = message.entities or message.caption_entities or []
        user_repo = await container.get(IUserRepository)
        for entity in entities:
            mentioned_id: int | None = None

            if entity.type == "mention":
                text = message.text or message.caption or ""
                raw = text[entity.offset : entity.offset + entity.length]
                username = raw.lstrip("@")
                user = await user_repo.get_by_username(username)
                if user is not None and not user.is_bot and user.id != message.from_user.id:
                    mentioned_id = user.id

            elif entity.type == "text_mention":
                if (
                    entity.user is not None
                    and not entity.user.is_bot
                    and entity.user.id != message.from_user.id
                ):
                    mentioned_id = entity.user.id

            if mentioned_id is not None:
                muted_user_ids.add(mentioned_id)

        if not muted_user_ids:
            return

        from bot.domain.tz import now_msk
        now = now_msk()

        for user_id in muted_user_ids:
            entry = await mute_repo.get(user_id, chat_id)
            if entry is None:
                continue
            if entry.until_at <= now:
                continue

            remaining_secs = int((entry.until_at - now).total_seconds())
            if remaining_secs <= 0:
                continue

            minutes, secs = divmod(remaining_secs, 60)
            if minutes > 0:
                time_str = f"{minutes} мин." + (f" {secs} сек." if secs else "")
            else:
                time_str = f"{secs} сек."

            muted_user = await user_repo.get_by_id(user_id)
            if muted_user is None:
                continue

            if muted_user.username:
                name = f"@{escape(muted_user.username)}"
            else:
                name = f"<b>{escape(muted_user.full_name)}</b>"

            try:
                notice = await message.reply(
                    f"🔇 {name} сейчас в муте — ещё {time_str}",
                    parse_mode=ParseMode.HTML,
                )
                schedule_delete(message.bot, notice, delay=30)
            except Exception as e:
                logger.debug("notify_muted: failed to send notice: %s", e)
