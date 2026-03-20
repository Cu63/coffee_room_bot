"""Хендлер /news — случайная IT-новость из RSS-лент.

Фичи:
- Источники берутся из config.yaml (news.feeds), а не захардкожены.
- LLM (AiTunnelClient) фильтрует кандидатов, оставляя IT-релевантные
  и позитивные новости, при необходимости перефразируя заголовок.
- Для пользователей не из списка admin.users действует hourly_limit:
  не более N вызовов /news за скользящий час (через Redis).
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import LinkPreviewOptions, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.infrastructure.aitunnel_client import AiTunnelClient
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.news_fetcher import NewsItem, fetch_random_news
from bot.infrastructure.redis.store import RedisStore

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


def _is_admin(username: str | None, admin_users: list[str]) -> bool:
    if not username:
        return False
    return username.lower() in {u.lower() for u in admin_users}


@router.message(Command("news"))
@inject
async def cmd_news(
    message: Message,
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
    llm: FromDishka[AiTunnelClient],
    redis: FromDishka[RedisStore],
) -> None:
    """Отправляет случайную IT-новость, отфильтрованную через LLM."""
    if message.from_user is None:
        return

    user_id = message.from_user.id
    username = message.from_user.username

    # ── Проверка hourly rate limit для не-админов ────────────────────────
    hourly_limit = config.news.hourly_limit
    if hourly_limit > 0 and not _is_admin(username, config.admin.users):
        count = await redis.news_hourly_count(user_id)
        if count >= hourly_limit:
            await message.reply(
                f"⏳ Лимит /news исчерпан: не более {hourly_limit} раз в час. "
                "Попробуй позже!"
            )
            return
        await redis.news_hourly_increment(user_id)

    # ── Получаем фиды из конфига ─────────────────────────────────────────
    feeds = config.news.as_tuples()

    thinking = await message.reply("🔍 Ищу свежие IT-новости…")

    items = await fetch_random_news(
        feeds=feeds,
        llm=llm if config.news.use_llm else None,
    )
    if not items:
        await thinking.edit_text(formatter._t["news_error"])
        return

    text = _format_news_item(items[0], formatter._t["news_item"])
    await thinking.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
