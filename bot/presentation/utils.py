"""Общие утилиты для presentation-слоя."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

logger = logging.getLogger(__name__)

# Единственное определение NO_PREVIEW для всего проекта
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Задержка автоудаления для обычных (не игровых) ответов бота
AUTO_DELETE_DELAY = 60

# ── Redis-очередь удалений ───────────────────────────────────────
# Sorted set: score = unix-timestamp когда удалять,
#             member = "{chat_id}:{message_id}"
# Хранится в общем Redis → переживает рестарты и работает
# корректно при любом количестве параллельных инстансов.

_DELETE_KEY = "bot:delete_queue"
_redis: aioredis.Redis | None = None

# Lua-скрипт: атомарно извлекает все созревшие элементы.
# Без Lua два инстанса могут прочитать один список и оба попытаются
# удалить одни и те же сообщения — Telegram вернёт ошибку, но это
# лишний API-вызов. С Lua каждое сообщение обрабатывается ровно одним
# инстансом.
_LUA_POP_DUE = """
local members = redis.call('ZRANGEBYSCORE', KEYS[1], 0, ARGV[1], 'LIMIT', 0, 20)
if #members > 0 then
    redis.call('ZREM', KEYS[1], unpack(members))
end
return members
"""


def init_redis(r: aioredis.Redis) -> None:
    """Инициализировать Redis-клиент для очереди удалений.

    Вызывается один раз в main() до запуска фоновых задач.
    """
    global _redis
    _redis = r


async def _zadd(chat_id: int, message_id: int, delete_at: float) -> None:
    """Добавить сообщение в Redis sorted set."""
    if _redis is None:
        return
    try:
        await _redis.zadd(_DELETE_KEY, {f"{chat_id}:{message_id}": delete_at})
    except Exception:
        logger.warning("delete_queue: не удалось записать в Redis", exc_info=True)


def schedule_delete(bot: Bot, *messages: Message, delay: int = 120) -> None:
    """Планирует удаление одного или нескольких сообщений через delay секунд."""
    delete_at = time.time() + delay
    for msg in messages:
        _fire(_zadd(msg.chat.id, msg.message_id, delete_at))


def schedule_delete_id(bot: Bot, chat_id: int, message_id: int, delay: int = 120) -> None:
    """Планирует удаление сообщения по chat_id и message_id через delay секунд."""
    _fire(_zadd(chat_id, message_id, time.time() + delay))


def _fire(coro) -> None:
    """Запускает корутину как fire-and-forget task в текущем event loop."""
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        # Нет запущенного loop — контекст, в котором schedule_delete
        # никогда не должен вызываться. Логируем и не падаем.
        logger.warning("schedule_delete вызван вне event loop — пропускаем")


async def delete_loop(bot: Bot, redis: aioredis.Redis) -> None:
    """Фоновый воркер: удаляет сообщения из Redis-очереди по мере созревания.

    Lua-скрипт делает извлечение атомарным: каждое сообщение обрабатывает
    ровно один инстанс даже при горизонтальном масштабировании.
    Между запросами пауза 50 мс (~20 удалений/сек максимум).
    """
    pop_due = redis.register_script(_LUA_POP_DUE)

    while True:
        try:
            members: list[str] = await pop_due(keys=[_DELETE_KEY], args=[time.time()])
            if members:
                for member in members:
                    try:
                        chat_id_str, msg_id_str = member.split(":", 1)
                        await bot.delete_message(int(chat_id_str), int(msg_id_str))
                    except TelegramBadRequest:
                        pass  # уже удалено — норма
                    except Exception:
                        pass
                    await asyncio.sleep(0.05)
            else:
                # Нет созревших элементов — ждём секунду
                await asyncio.sleep(1.0)
        except Exception:
            logger.exception("delete_loop: необработанная ошибка")
            await asyncio.sleep(5.0)


# ── Вспомогательные ─────────────────────────────────────────────


async def check_gameban(
    store: Any,
    user_id: int,
    chat_id: int,
    templates: dict[str, str],
) -> str | None:
    """Проверить самозапрет на игры. Возвращает текст ошибки или None."""
    import time as _time

    from bot.domain.bot_utils import format_duration

    until = await store.gameban_get_until(user_id, chat_id)
    if until is None:
        return None
    remaining = int(until - _time.time())
    if remaining <= 0:
        return None
    return templates["selfban_blocked"].format(remaining=format_duration(remaining))


async def safe_callback_answer(
    callback: CallbackQuery, text: str = "", show_alert: bool = False
) -> None:
    """Отвечает на callback-запрос, игнорируя ошибки устаревших запросов."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception:
        pass


async def reply_and_delete(
    message: Message, *args: Any, delay: int = AUTO_DELETE_DELAY, **kwargs: Any
) -> Message:
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