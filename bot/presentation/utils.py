"""Общие утилиты для presentation-слоя."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.types import LinkPreviewOptions, Message

logger = logging.getLogger(__name__)

# Единственное определение NO_PREVIEW для всего проекта
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Задержка автоудаления для обычных (не игровых) ответов бота
AUTO_DELETE_DELAY = 60


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def schedule_delete(bot: Bot, *messages: Message, delay: int = 120) -> None:
    """Планирует удаление одного или нескольких сообщений через delay секунд."""
    for msg in messages:
        asyncio.create_task(_delete_after(bot, msg.chat.id, msg.message_id, delay))


def schedule_delete_id(bot: Bot, chat_id: int, message_id: int, delay: int = 120) -> None:
    """Планирует удаление сообщения по chat_id и message_id через delay секунд."""
    asyncio.create_task(_delete_after(bot, chat_id, message_id, delay))


async def reply_and_delete(message: Message, *args: Any, delay: int = AUTO_DELETE_DELAY, **kwargs: Any) -> Message:
    """Отправляет reply и планирует его удаление через delay секунд."""
    reply = await message.reply(*args, **kwargs)
    if message.bot:
        schedule_delete(message.bot, reply, delay=delay)
    return reply
