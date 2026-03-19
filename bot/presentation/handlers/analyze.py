from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.analyze_service import AnalyzeRateLimitExceeded, AnalyzeService
from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig

logger = logging.getLogger(__name__)
router = Router(name="analyze")

_TG_LIMIT = 4096

_USERNAME_RE = re.compile(r"@([\w]+)")
_INT_RE = re.compile(r"^\d+$")

# Форматы времени: 30m, 2h, 1d, 1h30m, 90m, ...
_DURATION_RE = re.compile(
    r"^(?:(\d+)\s*(?:d|д|дн|days?))?"
    r"\s*(?:(\d+)\s*(?:h|ч|час?|hours?))?"
    r"\s*(?:(\d+)\s*(?:m|м|мин|min|minutes?))?$",
    re.IGNORECASE,
)
_SIMPLE_DURATION_RE = re.compile(r"^(\d+)\s*(m|м|мин|min|h|ч|час|d|д|дн)$", re.IGNORECASE)


def _parse_duration(token: str) -> timedelta | None:
    m = _SIMPLE_DURATION_RE.match(token)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit in ("m", "м", "мин", "min"):
            return timedelta(minutes=n)
        if unit in ("h", "ч", "час"):
            return timedelta(hours=n)
        if unit in ("d", "д", "дн"):
            return timedelta(days=n)

    m = _DURATION_RE.match(token)
    if m and any(m.groups()):
        days = int(m.group(1) or 0)
        hours = int(m.group(2) or 0)
        minutes = int(m.group(3) or 0)
        if days or hours or minutes:
            return timedelta(days=days, hours=hours, minutes=minutes)

    return None


def _parse_args(args: str | None) -> tuple[int, datetime | None, list[str]]:
    if not args:
        return 0, None, []

    tokens = args.strip().split()
    limit: int = 0
    since: datetime | None = None
    usernames: list[str] = []

    for token in tokens:
        if token.startswith("@"):
            usernames.append(token.lstrip("@").lower())
        elif limit == 0 and since is None:
            td = _parse_duration(token)
            if td is not None:
                since = datetime.now(TZ_MSK) - td
                limit = 10_000
            elif _INT_RE.match(token):
                limit = int(token)

    return limit, since, usernames


def _split_text(text: str, limit: int = _TG_LIMIT) -> list[str]:
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


async def _send_parts(message: Message, thinking: Message, text: str) -> None:
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
            logger.warning("HTML parse failed, falling back to plain text")
            stripped = re.sub(r"<[^>]+>", "", part)
            if first:
                await thinking.edit_text(stripped)
                first = False
            else:
                await message.answer(stripped)


def _rate_limit_text(used: int, limit: int) -> str:
    """Сообщение о превышении лимита токенов."""
    from datetime import date
    tomorrow = "завтра в 00:00 МСК"
    return (
        f"⛔ Дневной лимит токенов исчерпан.\n\n"
        f"Использовано: <b>{used:,}</b> / {limit:,} входящих токенов.\n"
        f"Лимит сбросится <b>{tomorrow}</b>."
    )


def _depth_limit_text(requested_hours: float, max_hours: int) -> str:
    return (
        f"❌ Слишком большой диапазон анализа: <b>{requested_hours:.0f} ч</b>.\n"
        f"Максимум — <b>{max_hours} ч</b>."
    )


@router.message(Command("analyze"))
@inject
async def cmd_analyze(
    message: Message,
    command: CommandObject,
    analyze_service: FromDishka[AnalyzeService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
) -> None:
    """/analyze [N | duration] [@user1 @user2 ...]"""
    if message.from_user is None:
        return

    user_id = message.from_user.id
    username = message.from_user.username
    limit, since, usernames = _parse_args(command.args)

    # ── Проверка глубины истории ─────────────────────────────────────────
    max_hours = config.analyze.max_history_hours
    if since is not None:
        age_hours = (datetime.now(TZ_MSK) - since).total_seconds() / 3600
        if age_hours > max_hours:
            await message.reply(
                _depth_limit_text(age_hours, max_hours),
                parse_mode=ParseMode.HTML,
            )
            return

    if limit == 0 and since is None:
        limit = config.analyze.max_messages

    limit = min(limit, config.analyze.max_messages)

    # ── Резолвим юзернеймы → user_id ────────────────────────────────────
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
            await message.reply(f"⚠️ Не нашёл в базе: {', '.join(unknown)} — они не учитываются.")

    thinking = await message.reply("🔍 Анализирую...")

    try:
        result = await analyze_service.analyze(
            chat_id=message.chat.id,
            user_id=user_id,
            username=username,
            n=limit,
            user_ids=user_ids,
            since=since,
        )
    except AnalyzeRateLimitExceeded as e:
        await thinking.edit_text(
            _rate_limit_text(e.used, e.limit),
            parse_mode=ParseMode.HTML,
        )
        return
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
    """/wir [N | duration] — Who Is Right."""
    if message.from_user is None:
        return

    user_id = message.from_user.id
    username = message.from_user.username
    limit, since, _ = _parse_args(command.args)

    # ── Проверка глубины истории ─────────────────────────────────────────
    max_hours = config.analyze.max_history_hours
    if since is not None:
        age_hours = (datetime.now(TZ_MSK) - since).total_seconds() / 3600
        if age_hours > max_hours:
            await message.reply(
                _depth_limit_text(age_hours, max_hours),
                parse_mode=ParseMode.HTML,
            )
            return

    if limit == 0 and since is None:
        limit = config.analyze.wir_default_messages

    if since is None:
        limit = min(limit, config.analyze.wir_max_messages)
    else:
        limit = 10_000

    thinking = await message.reply("⚖️ Разбираю ситуацию...")

    try:
        result = await analyze_service.wir(
            chat_id=message.chat.id,
            user_id=user_id,
            username=username,
            n=limit,
            since=since,
        )
    except AnalyzeRateLimitExceeded as e:
        await thinking.edit_text(
            _rate_limit_text(e.used, e.limit),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("wir: LLM request failed")
        await thinking.edit_text("❌ Что-то пошло не так. Спросите у администраторов.")
        return

    await _send_parts(message, thinking, result.text)
