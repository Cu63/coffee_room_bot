"""Общие утилиты для presentation-слоя."""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

logger = logging.getLogger(__name__)

# Единственное определение NO_PREVIEW для всего проекта
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Задержка автоудаления для обычных (не игровых) ответов бота
AUTO_DELETE_DELAY = 60

# Централизованная очередь удалений: (delete_at, chat_id, message_id)
# Используется heap для O(log n) вставки и извлечения минимума.
# Безопасно в asyncio (однопоточная среда) — синхронизация не нужна.
_delete_heap: list[tuple[float, int, int]] = []


def schedule_delete(bot: Bot, *messages: Message, delay: int = 120) -> None:
    """Планирует удаление одного или нескольких сообщений через delay секунд."""
    t = time.monotonic() + delay
    for msg in messages:
        heapq.heappush(_delete_heap, (t, msg.chat.id, msg.message_id))


def schedule_delete_id(bot: Bot, chat_id: int, message_id: int, delay: int = 120) -> None:
    """Планирует удаление сообщения по chat_id и message_id через delay секунд."""
    heapq.heappush(_delete_heap, (time.monotonic() + delay, chat_id, message_id))


async def delete_loop(bot: Bot) -> None:
    """Фоновый воркер: удаляет сообщения из очереди по одному.

    Вместо N параллельных задач (которые одновременно флудят Telegram и
    провоцируют 429 RetryAfter) используется один цикл с паузой 50 мс
    между запросами (~20 удалений/сек максимум).
    """
    while True:
        now = time.monotonic()
        if _delete_heap and _delete_heap[0][0] <= now:
            _, chat_id, message_id = heapq.heappop(_delete_heap)
            try:
                await bot.delete_message(chat_id, message_id)
            except Exception:
                pass
            # Пауза между запросами — не флудим Telegram API
            await asyncio.sleep(0.05)
        else:
            # Ждём до ближайшего запланированного удаления или максимум 2 сек
            wait = (_delete_heap[0][0] - now) if _delete_heap else 2.0
            await asyncio.sleep(min(wait, 2.0))


async def safe_callback_answer(
    callback: CallbackQuery, text: str = "", show_alert: bool = False
) -> None:
    """Отвечает на callback-запрос, игнорируя ошибки устаревших запросов."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def reply_and_delete(message: Message, *args: Any, delay: int = AUTO_DELETE_DELAY, **kwargs: Any) -> Message:
    """Отправляет reply и планирует его удаление через delay секунд.

    Если оригинальное сообщение уже удалено (бот не успел ответить),
    падает в answer() без reply_to_message_id.
    """
    try:
        reply = await message.reply(*args, **kwargs)
    except TelegramBadRequest as e:
        if "message to be replied not found" in str(e):
            reply = await message.answer(*args, **kwargs)
        else:
            raise
    if message.bot:
        schedule_delete(message.bot, reply, delay=delay)
    return reply
