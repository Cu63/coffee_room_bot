"""Хендлер /idea — голосование за идеи для бота.

Поток:
  1. /idea <текст>  → бот создаёт сообщение с кнопками 👍/👎
  2. Участники голосуют (каждый — один раз)
  3. При достижении порога голосов 👍 — уведомление в трекер (или DM админам)
"""

from __future__ import annotations

import json
import logging
import time

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, safe_callback_answer

logger = logging.getLogger(__name__)
router = Router(name="idea")

# Redis keys
_IDEA_PREFIX = "idea:"          # idea:{chat_id}:{idea_id} → JSON
_IDEA_COUNTER = "idea:counter"  # глобальный счётчик


def _idea_key(chat_id: int, idea_id: int) -> str:
    return f"{_IDEA_PREFIX}{chat_id}:{idea_id}"


def _idea_kb(idea_id: int, up: int, down: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"👍 {up}",
                    callback_data=f"idea:up:{idea_id}",
                ),
                InlineKeyboardButton(
                    text=f"👎 {down}",
                    callback_data=f"idea:down:{idea_id}",
                ),
            ]
        ]
    )


@router.message(Command("idea"), F.chat.type.in_({"group", "supergroup"}))
@inject
async def cmd_idea(
    message: Message,
    command: CommandObject,
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return

    text = (command.args or "").strip()
    if not text:
        await reply_and_delete(message, formatter._t["idea_usage"])
        return

    # Генерируем ID
    idea_id = await store._redis.incr(_IDEA_COUNTER)

    user = message.from_user
    display = user_link(user.username, user.full_name or "", user.id)

    # Формируем сообщение
    idea_text = formatter._t["idea_created"].format(
        id=idea_id, user=display, text=text, up=0, down=0,
    )
    reply = await message.reply(
        idea_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_idea_kb(idea_id, 0, 0),
    )

    # Сохраняем в Redis
    ttl_seconds = config.idea.vote_ttl_hours * 3600
    data = {
        "id": idea_id,
        "text": text,
        "author_id": user.id,
        "author_name": user.full_name or "",
        "author_username": user.username or "",
        "chat_id": message.chat.id,
        "message_id": reply.message_id,
        "up": [],       # список user_id проголосовавших 👍
        "down": [],     # список user_id проголосовавших 👎
        "notified": False,
        "created_at": time.time(),
    }
    await store._redis.set(
        _idea_key(message.chat.id, idea_id),
        json.dumps(data),
        ex=ttl_seconds,
    )

    # Удаляем исходную команду
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data.startswith("idea:"))
@inject
async def cb_idea_vote(
    callback: CallbackQuery,
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if callback.data is None or callback.message is None:
        await safe_callback_answer(callback)
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await safe_callback_answer(callback)
        return

    _, direction, idea_id_str = parts
    if direction not in ("up", "down"):
        await safe_callback_answer(callback)
        return

    try:
        idea_id = int(idea_id_str)
    except ValueError:
        await safe_callback_answer(callback)
        return

    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    key = _idea_key(chat_id, idea_id)

    raw = await store._redis.get(key)
    if raw is None:
        await safe_callback_answer(callback, formatter._t["idea_expired"], show_alert=True)
        return

    data = json.loads(raw)

    # Нельзя голосовать за свою идею
    if data["author_id"] == user_id:
        await safe_callback_answer(callback, formatter._t["idea_own_vote"], show_alert=True)
        return

    # Проверяем: уже голосовал?
    all_voters = set(data["up"]) | set(data["down"])
    if user_id in all_voters:
        await safe_callback_answer(callback, formatter._t["idea_already_voted"], show_alert=True)
        return

    # Записываем голос
    data[direction].append(user_id)

    # Сохраняем обратно с оставшимся TTL
    ttl = await store._redis.ttl(key)
    if ttl and ttl > 0:
        await store._redis.set(key, json.dumps(data), ex=ttl)
    else:
        await store._redis.set(key, json.dumps(data), ex=config.idea.vote_ttl_hours * 3600)

    up_count = len(data["up"])
    down_count = len(data["down"])

    # Обновляем кнопки
    display = user_link(
        data["author_username"] or None, data["author_name"], data["author_id"],
    )
    new_text = formatter._t["idea_created"].format(
        id=idea_id, user=display, text=data["text"], up=up_count, down=down_count,
    )
    try:
        await callback.message.edit_text(
            new_text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_idea_kb(idea_id, up_count, down_count),
        )
    except Exception:
        pass

    await safe_callback_answer(callback, formatter._t["idea_voted"])

    # Проверяем порог
    threshold = config.idea.votes_threshold
    if up_count >= threshold and not data.get("notified"):
        data["notified"] = True
        ttl = await store._redis.ttl(key)
        if ttl and ttl > 0:
            await store._redis.set(key, json.dumps(data), ex=ttl)

        # Отправляем в трекер
        await _notify_threshold(
            callback.bot, store, config, data, up_count,
            formatter,
        )


async def _notify_threshold(
    bot: Bot,
    store: RedisStore,
    config: AppConfig,
    data: dict,
    votes: int,
    formatter: MessageFormatter,
) -> None:
    """Уведомить админов, что идея набрала пороговое число голосов."""
    chat_id = data["chat_id"]
    author_display = user_link(
        data["author_username"] or None, data["author_name"], data["author_id"],
    )
    notify_text = formatter._t["idea_threshold"].format(
        id=data["id"],
        votes=votes,
        text=data["text"],
        author=author_display,
    )

    # Пытаемся отправить в трекер (feature-топик)
    tracker_chat_id = await store.tracker_get_tracker_id(chat_id)
    if tracker_chat_id:
        thread_id = await store.tracker_get_topic(tracker_chat_id, "feature")
        try:
            kwargs: dict = dict(chat_id=tracker_chat_id, text=notify_text, parse_mode=ParseMode.HTML)
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)
            return
        except Exception:
            logger.exception("idea: не удалось отправить в трекер %d", tracker_chat_id)

    # Фоллбэк: DM всем админам из конфига
    for username in config.admin.users:
        logger.info("idea: порог достигнут, трекер не настроен, уведомление в лог (admin: %s)", username)
