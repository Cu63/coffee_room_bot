"""Обработчик /genai — генерация картинки по описанию (GigaChat).

Роутер регистрируется только если задан GIGACHAT_API_KEY (см. bootstrap.py):
без ключа команда просто отсутствует, остальной бот работает как обычно.
"""

from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.genai_service import GenAiService
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.utils import reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="genai")


@router.message(Command("genai"), F.chat.type != "private")
@inject
async def cmd_genai(
    message: Message,
    command: CommandObject,
    genai_service: FromDishka[GenAiService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
    settings: FromDishka[BotSettings],
) -> None:
    """/genai <описание> — нарисовать картинку за {cost} кирчиков."""
    if message.from_user is None or message.bot is None:
        return

    cfg = config.genai
    p = formatter._p

    if not settings.gigachat_api_key:
        await reply_and_delete(message, formatter._t["genai_disabled"])
        return

    prompt = (command.args or "").strip()
    if not prompt:
        await reply_and_delete(
            message,
            formatter._t["genai_usage"].format(cost=cfg.cost, score_word=p.pluralize(cfg.cost)),
            parse_mode=ParseMode.HTML,
        )
        return
    if len(prompt) > cfg.max_prompt_length:
        await reply_and_delete(message, formatter._t["genai_too_long"].format(max=cfg.max_prompt_length))
        return

    thinking = await message.reply(formatter._t["genai_thinking"])
    result = await genai_service.generate(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        prompt=prompt,
        bot_id=message.bot.id,
    )

    if not result.success:
        if result.reason == "not_enough":
            text = formatter._t["genai_not_enough"].format(
                cost=result.cost,
                score_word=p.pluralize(result.cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            )
        elif result.reason == "no_image":
            text = formatter._t["genai_no_image"]
        else:
            text = formatter._t["genai_error"]
        await thinking.edit_text(text)
        return

    caption = formatter._t["genai_caption"].format(
        prompt=escape(prompt),
        cost=result.cost,
        score_word=p.pluralize(result.cost),
        balance=result.new_balance,
        score_word_balance=p.pluralize(result.new_balance),
    )
    try:
        await message.reply_photo(
            BufferedInputFile(result.image or b"", filename="genai.jpg"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        # Картинку сгенерировали, но в чат она не ушла — возвращаем баллы
        logger.exception("genai: не удалось отправить картинку")
        await genai_service.refund(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            bot_id=message.bot.id,
            cost=result.cost,
        )
        await thinking.edit_text(formatter._t["genai_send_failed"])
        return

    try:
        await thinking.delete()
    except Exception:
        pass
