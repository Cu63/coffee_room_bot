"""Хендлер /news — случайная новость из RSS-лент российских СМИ."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import LinkPreviewOptions, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.news_fetcher import NewsItem, fetch_random_news

logger = logging.getLogger(__name__)
router = Router(name="news")


def _format_news_item(item: NewsItem, template: str) -> str:
    """Форматирует одну новость в Telegram HTML по шаблону из messages.yaml."""
    desc = item.description[:300]
    if len(item.description) > 300:
        desc += "…"
    return template.format(
        title=item.title,
        description=desc,
        url=item.url,
        source=item.source,
    )


@router.message(Command("news"))
@inject
async def cmd_news(
    message: Message,
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Отправляет случайную новость из российских RSS-лент."""
    items = await fetch_random_news(max_items=1)
    if not items:
        await message.reply(formatter._t["news_error"])
        return

    text = _format_news_item(items[0], formatter._t["news_item"])
    await message.reply(
        text,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
