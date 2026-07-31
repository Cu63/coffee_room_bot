"""Обработчик /ff — фанфик про участников чата.

Синтаксис: /ff @user1 @user2 ... [сюжет фанфика]
Все ведущие @упоминания — герои, остаток строки — пожелание к сюжету.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.fanfic_service import FanficService
from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.entities import User
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.utils import reply_and_delete, send_html_parts

logger = logging.getLogger(__name__)
router = Router(name="fanfic")


def _parse_args(args: str | None) -> tuple[list[str], str]:
    """Делит аргументы на список юзернеймов (без @) и текст сюжета.

    Героями считаются только @упоминания в начале — как только пошёл
    обычный текст, всё остальное уходит в сюжет.
    """
    if not args:
        return [], ""

    tokens = args.strip().split()
    usernames: list[str] = []
    idx = 0
    for idx, token in enumerate(tokens):
        if not token.startswith("@") or len(token) < 2:
            break
        uname = token.lstrip("@").lower()
        if uname not in usernames:
            usernames.append(uname)
    else:
        # Все токены оказались упоминаниями — сюжета нет
        return usernames, ""

    return usernames, " ".join(tokens[idx:]).strip()


@router.message(Command("ff"), F.chat.type != "private")
@inject
async def cmd_fanfic(
    message: Message,
    command: CommandObject,
    fanfic_service: FromDishka[FanficService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/ff @user1 @user2 ... [сюжет]"""
    if message.from_user is None:
        return

    cfg = config.fanfic
    usernames, prompt = _parse_args(command.args)

    if not usernames:
        await reply_and_delete(
            message,
            formatter._t["ff_usage"].format(messages=cfg.messages_per_user),
        )
        return
    if len(usernames) > cfg.max_users:
        await reply_and_delete(message, formatter._t["ff_too_many"].format(max=cfg.max_users))
        return
    if len(prompt) > cfg.max_prompt_length:
        await reply_and_delete(message, formatter._t["ff_too_long"].format(max=cfg.max_prompt_length))
        return

    # ── Резолвим юзернеймы → пользователей ───────────────────────────
    heroes: list[User] = []
    unknown: list[str] = []
    for uname in usernames:
        user = await user_repo.get_by_username(uname)
        if user is None:
            unknown.append(f"@{uname}")
        else:
            heroes.append(user)

    if not heroes:
        await reply_and_delete(
            message, formatter._t["ff_unknown_users"].format(users=", ".join(unknown))
        )
        return
    if unknown:
        await reply_and_delete(
            message, formatter._t["ff_partial_users"].format(users=", ".join(unknown))
        )

    thinking = await message.reply(formatter._t["ff_thinking"])
    try:
        result = await fanfic_service.write(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            heroes=heroes,
            prompt=prompt,
        )
    except Exception:
        logger.exception("ff: запрос к LLM не удался")
        await thinking.edit_text(formatter._t["ff_error"])
        return

    await send_html_parts(message, thinking, result.text)
