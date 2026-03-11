"""Общие утилиты для presentation-слоя."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger(__name__)

AUTO_DELETE_SECONDS = 120  # секунды


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass  # уже удалено или нет прав — игнорируем


def schedule_delete(bot: Bot, *messages: Message, delay: int = AUTO_DELETE_SECONDS) -> None:
    """Планирует удаление одного или нескольких сообщений через delay секунд."""
    for msg in messages:
        asyncio.create_task(_delete_after(bot, msg.chat.id, msg.message_id, delay))