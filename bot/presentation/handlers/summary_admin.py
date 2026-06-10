"""Админская команда /summary_admin — тестовая сводка за последние 24 часа.

Не меняет никакой стейт. И команда, и сообщение со сводкой автоматически
удаляются через 5 минут.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.message_repository import IMessageRepository
from bot.application.summary_service import generate_daily_summary
from bot.domain.bot_utils import is_admin
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient
from bot.presentation.utils import schedule_delete

logger = logging.getLogger(__name__)
router = Router(name="summary_admin")

_TG_LIMIT = 4096
_AUTO_DELETE_SEC = 5 * 60  # 5 минут — и команда, и саммари


def _split_text(text: str, limit: int = _TG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    return parts


async def _send_summary_parts(bot: Bot, chat_id: int, text: str) -> list[Message]:
    sent: list[Message] = []
    for part in _split_text(text):
        try:
            msg = await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            logger.warning("summary_admin: HTML invalid, falling back to plain text")
            plain = re.sub(r"<[^>]+>", "", part)
            msg = await bot.send_message(chat_id, plain)
        sent.append(msg)
    return sent


@router.message(Command("summary_admin"))
@inject
async def cmd_summary_admin(
    message: Message,
    bot: Bot,
    config: FromDishka[AppConfig],
    client: FromDishka[OpenAiClient],
    message_repo: FromDishka[IMessageRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Тест-прогон ежедневной сводки за последние 24 часа.

    Доступно только администраторам из configs/config.yaml → admin.users.
    Команда и итоговое сообщение удаляются через 5 минут.
    """
    if message.from_user is None:
        return

    # Авто-удаление самой команды через 5 минут (срабатывает в любом случае).
    schedule_delete(bot, message, delay=_AUTO_DELETE_SEC)

    if not is_admin(message.from_user.username, config.admin.users):
        deny = await message.reply(formatter._t["admin_not_allowed"])
        schedule_delete(bot, deny, delay=_AUTO_DELETE_SEC)
        return

    cfg = config.daily_summary
    now = datetime.now(TZ_MSK)
    since = now - timedelta(hours=24)
    date_str = now.strftime("%d.%m.%Y")

    thinking = await message.reply("📰 Готовлю сводку за последние 24 часа...")

    try:
        messages = await message_repo.get_recent_with_text(
            message.chat.id, cfg.max_messages, since=since
        )

        if not messages:
            await thinking.edit_text("За последние 24 часа сообщений не найдено.")
            schedule_delete(bot, thinking, delay=_AUTO_DELETE_SEC)
            return

        text = await generate_daily_summary(
            client, formatter, cfg, messages, date_str,
            admin_prefix=config.admin.prefix,
        )
        logger.info(
            "summary_admin: chat=%d user=%d messages=%d done",
            message.chat.id,
            message.from_user.id,
            len(messages),
        )
    except Exception:
        logger.exception("summary_admin: failed")
        await thinking.edit_text("❌ Не удалось получить сводку. Смотри логи.")
        schedule_delete(bot, thinking, delay=_AUTO_DELETE_SEC)
        return

    # «thinking» больше не нужен — удалим вместе с остальным через 5 минут.
    schedule_delete(bot, thinking, delay=_AUTO_DELETE_SEC)

    sent = await _send_summary_parts(bot, message.chat.id, text)
    for m in sent:
        schedule_delete(bot, m, delay=_AUTO_DELETE_SEC)
