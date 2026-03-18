from __future__ import annotations

import logging
import re

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.analyze_service import AnalyzeService
from bot.application.interfaces.user_repository import IUserRepository
from bot.infrastructure.config_loader import AppConfig

logger = logging.getLogger(__name__)
router = Router(name="analyze")

# Telegram ограничивает одно сообщение 4096 символами
_TG_LIMIT = 4096

_USERNAME_RE = re.compile(r"@([\w]+)")
_INT_RE = re.compile(r"^\d+$")


# ── Утилиты ──────────────────────────────────────────────────────────────────

def _split_text(text: str, limit: int = _TG_LIMIT) -> list[str]:
    """Разбить текст на части по ``limit`` символов, не разрывая слова."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit  # нет пробела — режем жёстко (крайний случай)
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    return parts


async def _send_parts(message: Message, thinking: Message, text: str) -> None:
    """Отправить ответ, разбив на части если нужно. Первая часть — edit thinking."""
    parts = _split_text(text)
    first = True
    for part in parts:
        try:
            if first:
                await thinking.edit_text(part, parse_mode=ParseMode.HTML)
                first = False
            else:
                await message.answer(part, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            logger.warning("HTML parse failed for analyze response, falling back to plain text")
            stripped = re.sub(r"<[^>]+>", "", part)
            if first:
                await thinking.edit_text(stripped)
                first = False
            else:
                await message.answer(stripped)


def _parse_analyze_args(args: str | None) -> tuple[int | None, list[str]]:
    """Разобрать аргументы /analyze [N] [@user1 @user2 ...].

    Возвращает (n_or_None, список_юзернеймов_без_@).
    N может быть первым аргументом или вообще отсутствовать.
    """
    if not args:
        return None, []

    tokens = args.strip().split()
    n: int | None = None
    usernames: list[str] = []

    for token in tokens:
        if token.startswith("@"):
            usernames.append(token.lstrip("@").lower())
        elif _INT_RE.match(token) and n is None:
            n = int(token)
        # Остальное игнорируем

    return n, usernames


# ── Хендлеры ─────────────────────────────────────────────────────────────────

@router.message(Command("analyze"))
@inject
async def cmd_analyze(
    message: Message,
    command: CommandObject,
    analyze_service: FromDishka[AnalyzeService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
) -> None:
    """/analyze [N] [@user1 @user2 ...]

    Анализирует последние N сообщений чата (или конкретных пользователей).
    N не может превышать analyze.max_messages из конфига.
    """
    n_raw, usernames = _parse_analyze_args(command.args)
    n = min(n_raw or config.analyze.max_messages, config.analyze.max_messages)

    # Резолвим юзернеймы → user_id
    user_ids: list[int] | None = None
    if usernames:
        resolved: list[int] = []
        unknown: list[str] = []
        for uname in usernames:
            user = await user_repo.get_by_username(uname)
            if user:
                resolved.append(user.id)
            else:
                unknown.append(f"@{uname}")

        if not resolved:
            await message.reply(
                f"Не найдено ни одного из указанных пользователей: {', '.join(unknown)}"
            )
            return

        user_ids = resolved
        if unknown:
            await message.reply(
                f"⚠️ Не нашёл в базе: {', '.join(unknown)} — они не учитываются."
            )

    thinking = await message.reply("🔍 Анализирую...")

    try:
        result = await analyze_service.analyze(
            chat_id=message.chat.id,
            n=n,
            user_ids=user_ids,
        )
    except Exception:
        logger.exception("analyze: LLM request failed")
        await thinking.edit_text("❌ Что-то пошло не так. Спросите у администраторов.")
        return

    await _send_parts(message, thinking, result.text)


@router.message(Command("wir"))
@inject
async def cmd_wir(
    message: Message,
    command: CommandObject,
    analyze_service: FromDishka[AnalyzeService],
    config: FromDishka[AppConfig],
) -> None:
    """/wir [N] — Who Is Right.

    Анализирует последние N сообщений, ищет конфликт, манипуляции,
    определяет кто прав и предлагает урегулирование.
    N по умолчанию = analyze.wir_default_messages, максимум = analyze.wir_max_messages.
    """
    n_raw: int | None = None
    if command.args:
        tokens = command.args.strip().split()
        if tokens and _INT_RE.match(tokens[0]):
            n_raw = int(tokens[0])

    n = min(n_raw or config.analyze.wir_default_messages, config.analyze.wir_max_messages)

    thinking = await message.reply("⚖️ Разбираю ситуацию...")

    try:
        result = await analyze_service.wir(chat_id=message.chat.id, n=n)
    except Exception:
        logger.exception("wir: LLM request failed")
        await thinking.edit_text("❌ Что-то пошло не так. Спросите у администраторов.")
        return

    await _send_parts(message, thinking, result.text)