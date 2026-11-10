"""Хендлеры режимов чата: /silence, /gif, /chatmode off."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.chatmode_service import ChatmodeService
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import can_moderate
from bot.presentation.utils import reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="chatmode")


def _label(t: dict[str, str], mode: str) -> str:
    return {"silence": t["chatmode_label_silence"], "gif": t["chatmode_label_gif"]}.get(mode, mode)


def _format_remaining(entry) -> str:
    from datetime import datetime

    from bot.domain.bot_utils import format_duration
    from bot.domain.tz import TZ_MSK

    secs = max(0, int((entry.expires_at - datetime.now(TZ_MSK)).total_seconds()))
    return format_duration(secs)


async def _cmd_mode(
    message: Message,
    command: CommandObject,
    service: ChatmodeService,
    config: AppConfig,
    pluralizer: ScorePluralizer,
    formatter: MessageFormatter,
    mode: str,
) -> None:
    t = formatter._t
    if message.from_user is None or message.bot is None:
        return
    if message.chat.type not in ("group", "supergroup"):
        await reply_and_delete(message, t["chatmode_groups_only"])
        return

    cfg = config.chatmode
    if not cfg.enabled:
        await reply_and_delete(message, t["chatmode_disabled"])
        return

    mode_cfg = cfg.silence if mode == "silence" else cfg.gif

    # Парсим минуты из аргумента
    minutes = cfg.default_minutes
    if command.args:
        try:
            minutes = int(command.args.strip())
        except ValueError:
            await reply_and_delete(message, t["chatmode_bad_minutes"].format(mode=mode))
            return

    if minutes < 1:
        await reply_and_delete(message, t["chatmode_min_minutes"])
        return
    if minutes > mode_cfg.max_minutes:
        await reply_and_delete(message, t["chatmode_max_minutes"].format(max=mode_cfg.max_minutes))
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    total_cost = mode_cfg.cost_per_minute * minutes

    result = await service.activate(
        bot=message.bot,
        chat_id=chat_id,
        user_id=user_id,
        mode=mode,
        minutes=minutes,
        cost_per_minute=mode_cfg.cost_per_minute,
        bot_id=message.bot.id,
    )

    if not result.success:
        if result.error == "already_active":
            existing = await service.get_active(chat_id)
            if existing:
                await reply_and_delete(
                    message,
                    t["chatmode_already_active"].format(
                        label=_label(t, existing.mode), remaining=_format_remaining(existing)
                    ),
                    parse_mode=ParseMode.HTML,
                )
            else:
                await reply_and_delete(message, t["chatmode_already_active_short"])
        elif result.error == "not_enough":
            await reply_and_delete(
                message,
                t["chatmode_not_enough"].format(
                    currency=pluralizer.pluralize(0),
                    cost=total_cost,
                    score_word=pluralizer.pluralize(total_cost),
                    balance=result.new_balance,
                ),
                parse_mode=ParseMode.HTML,
            )
        else:
            await reply_and_delete(message, t["chatmode_error"].format(error=result.error))
        return

    # Успех
    label = _label(t, mode)
    score_word = pluralizer.pluralize(result.cost)
    initiator = user_link(
        message.from_user.username,
        message.from_user.full_name,
        user_id,
    )
    balance_word = pluralizer.pluralize(result.new_balance)

    try:
        await message.delete()
    except Exception:
        pass

    await message.bot.send_message(
        chat_id,
        t["chatmode_activated"].format(
            label=label,
            minutes=minutes,
            initiator=initiator,
            cost=result.cost,
            score_word=score_word,
            balance=result.new_balance,
            balance_word=balance_word,
        ),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("silence"))
@inject
async def cmd_silence(
    message: Message,
    command: CommandObject,
    service: FromDishka[ChatmodeService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    await _cmd_mode(message, command, service, config, pluralizer, formatter, "silence")


@router.message(Command("gif"))
@inject
async def cmd_gif(
    message: Message,
    command: CommandObject,
    service: FromDishka[ChatmodeService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    await _cmd_mode(message, command, service, config, pluralizer, formatter, "gif")


@router.message(Command("chatmode"))
@inject
async def cmd_chatmode(
    message: Message,
    command: CommandObject,
    service: FromDishka[ChatmodeService],
    config: FromDishka[AppConfig],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Управление активным режимом. Сейчас только: /chatmode off"""
    t = formatter._t
    if message.from_user is None or message.bot is None:
        return
    if message.chat.type not in ("group", "supergroup"):
        await reply_and_delete(message, t["chatmode_groups_only"])
        return

    arg = (command.args or "").strip().lower()
    if arg != "off":
        await reply_and_delete(message, t["chatmode_usage"])
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    entry = await service.get_active(chat_id)
    if entry is None:
        await reply_and_delete(message, t["chatmode_none"])
        return

    # Отменить может инициатор, админ бота или модератор чата (не купленный /buyop титул)
    if entry.activated_by != user_id and not await can_moderate(
        message.bot, chat_id, message.from_user, config.admin.users
    ):
        await reply_and_delete(message, t["chatmode_cancel_not_allowed"])
        return

    await service.deactivate(message.bot, entry)

    label = _label(t, entry.mode)
    try:
        await message.delete()
    except Exception:
        pass

    await message.bot.send_message(
        chat_id,
        t["chatmode_cancelled"].format(label=label),
        parse_mode=ParseMode.HTML,
    )
