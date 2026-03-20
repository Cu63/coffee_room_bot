"""Хендлер /daily — предпросмотр лидерборда активности."""
from __future__ import annotations

from datetime import timedelta

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.daily_leaderboard_service import DailyLeaderboardService
from bot.domain.tz import now_msk
from bot.infrastructure.config_loader import AppConfig
from bot.presentation.utils import reply_and_delete

router = Router(name="daily")


@router.message(Command("daily"))
@inject
async def cmd_daily(
    message: Message,
    service: FromDishka[DailyLeaderboardService],
    config: FromDishka[AppConfig],
) -> None:
    if message.chat.type == "private":
        await reply_and_delete(message, "❌ Команда доступна только в групповых чатах.")
        return

    if not config.daily_leaderboard.enabled:
        await reply_and_delete(message, "❌ Ежедневный лидерборд отключён.")
        return

    chat_id = message.chat.id
    today = now_msk().date()
    yesterday = today - timedelta(days=1)

    today_lb = await service.get_leaderboard(chat_id, today)
    yesterday_lb = await service.get_leaderboard(chat_id, yesterday)

    text = service.format_preview(
        today=today_lb,
        yesterday=yesterday_lb if not yesterday_lb.is_empty() else None,
    )

    sent = await message.answer(text, parse_mode=ParseMode.HTML)
    # Удаляем исходную команду
    try:
        await message.delete()
    except Exception:
        pass
