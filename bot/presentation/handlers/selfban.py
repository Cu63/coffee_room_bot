"""Хендлер /selfban — самозапрет на участие в играх."""

from __future__ import annotations

import logging
import time

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.domain.bot_utils import format_duration, parse_duration
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="selfban")


@router.message(Command("selfban"))
@inject
async def cmd_selfban(
    message: Message,
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    cfg = config.selfban

    args = (message.text or "").split()[1:]

    if not args:
        await reply_and_delete(
            message,
            formatter._t["selfban_usage"].format(
                min=cfg.min_minutes,
                max=cfg.max_minutes,
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # Парсим длительность
    seconds = parse_duration(args[0])
    if seconds is None or seconds <= 0:
        await reply_and_delete(
            message,
            formatter._t["selfban_usage"].format(
                min=cfg.min_minutes,
                max=cfg.max_minutes,
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    minutes = seconds / 60
    if minutes < cfg.min_minutes or minutes > cfg.max_minutes:
        await reply_and_delete(
            message,
            formatter._t["selfban_invalid"].format(
                min=cfg.min_minutes,
                max=cfg.max_minutes,
            ),
        )
        return

    # Проверяем, есть ли уже активный запрет
    existing = await store.gameban_get_until(user_id, chat_id)
    if existing is not None:
        remaining = int(existing - time.time())
        if remaining > 0:
            await reply_and_delete(
                message,
                formatter._t["selfban_already"].format(
                    remaining=format_duration(remaining),
                ),
            )
            return

    until_ts = time.time() + seconds
    await store.gameban_set(user_id, chat_id, until_ts)

    await reply_and_delete(
        message,
        formatter._t["selfban_success"].format(
            duration=format_duration(seconds),
        ),
        delay=120,
    )
