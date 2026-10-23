"""Обработчик /think — случайная мысль участника по его IQ и свежей переписке."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.iq_service import IqService
from bot.application.think_service import ThinkRateLimitExceeded, ThinkService, Thought
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.middlewares.llm_busy import OneLlmRequestPerUserMiddleware
from bot.presentation.utils import NO_PREVIEW

logger = logging.getLogger(__name__)
router = Router(name="think")
router.message.middleware(OneLlmRequestPerUserMiddleware())


def _footer(thought: Thought, formatter: MessageFormatter) -> str:
    if thought.is_admin or thought.daily_limit <= 0:
        return formatter._t["think_left_admin"].format(used=thought.used_today)
    left = max(0, thought.daily_limit - thought.used_today)
    return formatter._t["think_left"].format(left=left, limit=thought.daily_limit)


@router.message(Command("think"), F.chat.type != "private")
@inject
async def cmd_think(
    message: Message,
    think_service: FromDishka[ThinkService],
    iq_service: FromDishka[IqService],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """/think — мысль вызвавшего: умнее или глупее в зависимости от его IQ в этом чате."""
    if message.from_user is None:
        return

    user = message.from_user
    iq = await iq_service.get_iq(user.id, message.chat.id)
    thinking = await message.reply(formatter._t["think_thinking"])
    try:
        thought = await think_service.think(message.chat.id, user.id, user.username, user.full_name, iq)
    except ThinkRateLimitExceeded as e:
        await thinking.edit_text(formatter._t["think_rate_limit"].format(used=e.used, limit=e.limit))
        return
    except Exception:
        logger.exception("think: запрос к LLM не удался")
        await thinking.edit_text(formatter._t["think_error"])
        return

    await thinking.edit_text(
        formatter._t["think_result"].format(
            user=user_link(user.username, user.full_name, user.id),
            iq=iq,
            thought=html.escape(thought.text),  # текст модели — без разметки, только текст
            footer=_footer(thought, formatter),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
