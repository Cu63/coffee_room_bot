"""Фоновая задача: ежедневная сводка чата через OpenAI."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.domain.tz import TZ_MSK

logger = logging.getLogger(__name__)

# Telegram лимит на одно сообщение
_TG_LIMIT = 4096


def _seconds_until(target_time: str) -> float:
    """Секунды до следующего наступления HH:MM (MSK).

    Если время уже прошло сегодня — считаем до завтра.
    """
    now = datetime.now(TZ_MSK)
    hh, mm = map(int, target_time.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _split_text(text: str, limit: int = _TG_LIMIT) -> list[str]:
    """Разбить текст на части не разрывая слова."""
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


async def _send_summary(bot: Bot, chat_id: int, text: str) -> None:
    parts = _split_text(text)
    for part in parts:
        try:
            await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            # HTML невалиден — шлём plain text
            import re
            plain = re.sub(r"<[^>]+>", "", part)
            await bot.send_message(chat_id, plain)


async def _run_once(bot: Bot, container) -> None:
    """Один прогон: генерируем и отправляем сводку во все активные чаты."""
    from bot.application.analyze_service import _format_messages
    from bot.application.interfaces.message_repository import IMessageRepository
    from bot.infrastructure.config_loader import AppConfig
    from bot.infrastructure.message_formatter import MessageFormatter
    from bot.infrastructure.openai_client import OpenAiClient

    async with container() as scope:
        config: AppConfig = await scope.get(AppConfig)
        cfg = config.daily_summary
        client: OpenAiClient = await scope.get(OpenAiClient)
        message_repo: IMessageRepository = await scope.get(IMessageRepository)
        formatter: MessageFormatter = await scope.get(MessageFormatter)

        chat_ids = await message_repo.get_active_chats()

    if not chat_ids:
        logger.info("daily_summary: no active chats, skipping")
        return

    now = datetime.now(TZ_MSK)
    # Начало текущего дня (00:00 MSK)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = now.strftime("%d.%m.%Y")
    logger.info("daily_summary: running for %d chats, since %s", len(chat_ids), start_of_day)

    for chat_id in chat_ids:
        try:
            async with container() as scope:
                message_repo = await scope.get(IMessageRepository)
                messages = await message_repo.get_recent_with_text(
                    chat_id, cfg.max_messages, since=start_of_day
                )

            if not messages:
                logger.debug("daily_summary: chat %d has no messages, skipping", chat_id)
                continue

            user_prompt = formatter._t["daily_summary_user_prompt"].format(
                actual=len(messages),
                messages=_format_messages(messages),
                date=today_str,
            )

            resp = await client.chat([
                {"role": "system", "content": formatter._t["daily_summary_system_prompt"]},
                {"role": "user", "content": user_prompt},
            ])

            text = resp.text or "Нет ответа от модели."
            logger.info(
                "daily_summary: chat=%d messages=%d in=%d out=%d",
                chat_id, len(messages), resp.input_tokens, resp.output_tokens,
            )

            await _send_summary(bot, chat_id, text)

        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("daily_summary: telegram error in chat %d: %s", chat_id, e)
        except Exception:
            logger.exception("daily_summary: failed for chat %d", chat_id)


async def daily_summary_loop(bot: Bot, container) -> None:
    """Ждёт до нужного времени, отправляет сводку, повторяет каждые 24 часа."""
    from bot.infrastructure.config_loader import AppConfig

    # Читаем конфиг один раз при старте
    async with container() as scope:
        config: AppConfig = await scope.get(AppConfig)
        cfg = config.daily_summary

    if not cfg.enabled:
        logger.info("daily_summary: disabled, loop not started")
        return

    logger.info("daily_summary: scheduled at %s MSK", cfg.time)

    while True:
        wait = _seconds_until(cfg.time)
        logger.debug("daily_summary: sleeping %.0f seconds until %s", wait, cfg.time)
        await asyncio.sleep(wait)

        try:
            await _run_once(bot, container)
        except Exception:
            logger.exception("daily_summary: unexpected error in _run_once")

        # Небольшая пауза чтобы не запустить дважды если sleep вернулся чуть раньше
        await asyncio.sleep(60)