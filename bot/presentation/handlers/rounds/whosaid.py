"""/whosaid — «кто это сказал?»: угадать автора сообщения из чата."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.whosaid_service import WhoSaidService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.rounds.common import post_round, reserve_round
from bot.presentation.utils import reply_and_delete

router = Router(name="whosaid")


@router.message(Command("whosaid"), F.chat.type != "private")
@inject
async def cmd_whosaid(
    message: Message,
    whosaid_service: FromDishka[WhoSaidService],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Цитата из чата и четыре кандидата в авторы; автор цитаты не отвечает."""
    cfg = config.whosaid
    round_id = await reserve_round(message, store, formatter, "whosaid", cfg)
    if round_id is None:
        return
    question = await whosaid_service.pick(message.chat.id)
    if question is None:
        await store.round_release_chat(message.chat.id, round_id)
        await reply_and_delete(message, formatter._t["whosaid_not_enough"])
        return
    await post_round(message, store, formatter, "whosaid", cfg, round_id, question)
