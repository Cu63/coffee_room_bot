"""Фоновая задача: ежедневный лидерборд активности."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.domain.tz import TZ_MSK

logger = logging.getLogger(__name__)


def _seconds_until(target_time: str) -> float:
    """Секунды до следующего наступления HH:MM (MSK)."""
    now = datetime.now(TZ_MSK)
    hh, mm = map(int, target_time.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _run_once(bot: Bot, container) -> None:
    """Один прогон: собираем лидеров и отправляем во все активные чаты."""
    from bot.application.daily_leaderboard_service import DailyLeaderboardService
    from bot.application.interfaces.daily_leaderboard_repository import IDailyLeaderboardRepository
    from bot.infrastructure.config_loader import AppConfig

    now = datetime.now(TZ_MSK)
    today = now.date()

    # Получаем список чатов и конфиг один раз
    async with container() as scope:
        config: AppConfig = await scope.get(AppConfig)
        cfg = config.daily_leaderboard
        repo: IDailyLeaderboardRepository = await scope.get(IDailyLeaderboardRepository)
        chat_ids = await repo.get_active_chats()

    if not chat_ids:
        logger.info("daily_leaderboard: no active chats, skipping")
        return

    bonuses = {
        "messages": cfg.bonus_messages,
        "reactions_given": cfg.bonus_reactions_given,
        "reactions_received": cfg.bonus_reactions_received,
        "replies": cfg.bonus_replies,
        "ttt_wins": cfg.bonus_ttt_wins,
        "wordgame_wins": cfg.bonus_wordgame_wins,
    }

    logger.info("daily_leaderboard: running for %d chats (%s)", len(chat_ids), today)

    for chat_id in chat_ids:
        try:
            async with container() as scope:
                service: DailyLeaderboardService = await scope.get(DailyLeaderboardService)
                lb = await service.get_leaderboard(chat_id, today)

                if lb.is_empty():
                    logger.debug("daily_leaderboard: chat %d has no activity, skipping", chat_id)
                    continue

                text = await service.award_and_format(chat_id, lb, bonuses)

            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)

        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("daily_leaderboard: telegram error in chat %d: %s", chat_id, e)
        except Exception:
            logger.exception("daily_leaderboard: failed for chat %d", chat_id)


async def daily_leaderboard_loop(bot: Bot, container) -> None:
    """Ждёт нужного времени MSK, подводит итоги, повторяет каждые 24 часа."""
    from bot.infrastructure.config_loader import AppConfig

    async with container() as scope:
        config: AppConfig = await scope.get(AppConfig)
        cfg = config.daily_leaderboard

    if not cfg.enabled:
        logger.info("daily_leaderboard: disabled, loop not started")
        return

    logger.info("daily_leaderboard: scheduled at %s MSK", cfg.time)

    while True:
        wait = _seconds_until(cfg.time)
        logger.debug("daily_leaderboard: sleeping %.0f s until %s MSK", wait, cfg.time)
        await asyncio.sleep(wait)

        try:
            await _run_once(bot, container)
        except Exception:
            logger.exception("daily_leaderboard: unexpected error in _run_once")

        # Небольшая пауза чтобы не сработать дважды
        await asyncio.sleep(60)
