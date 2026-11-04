"""/quiz — викторина: вопрос придумывает LLM, сложность — по IQ вызвавшего."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.iq_service import IqService
from bot.application.quiz_service import QuizService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.rounds.common import post_round, reserve_round
from bot.presentation.middlewares.llm_busy import OneLlmRequestPerUserMiddleware

logger = logging.getLogger(__name__)
router = Router(name="quiz")
router.message.middleware(OneLlmRequestPerUserMiddleware())

_MAX_TOPIC = 100


@router.message(Command("quiz"), F.chat.type != "private")
@inject
async def cmd_quiz(
    message: Message,
    command: CommandObject,
    quiz_service: FromDishka[QuizService],
    iq_service: FromDishka[IqService],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/quiz [тема] — вопрос с четырьмя вариантами; первый правильный ответ получает приз."""
    if message.from_user is None:
        return
    cfg = config.quiz
    chat_id = message.chat.id
    round_id = await reserve_round(message, store, formatter, "quiz", cfg)
    if round_id is None:
        return
    placeholder = await message.reply(formatter._t["quiz_thinking"])
    try:
        iq = await iq_service.get_iq(message.from_user.id, chat_id)
        topic = (command.args or "").strip()[:_MAX_TOPIC]
        question = await quiz_service.generate(chat_id, message.from_user.id, iq, topic)
    except Exception:
        logger.exception("quiz: вопрос не получен")
        await store.round_release_chat(chat_id, round_id)
        await placeholder.edit_text(formatter._t["quiz_error"])
        return
    await post_round(message, store, formatter, "quiz", cfg, round_id, question, placeholder)
