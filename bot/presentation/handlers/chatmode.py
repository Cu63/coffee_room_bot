"""Хендлеры режимов чата: /silence, /gif, /chatmode off."""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.chatmode_service import ChatmodeService
from bot.domain.bot_utils import is_admin
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import user_link
from bot.presentation.utils import reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="chatmode")

_MODE_LABEL = {
    "silence": "🤫 Тишина",
    "gif": "🎭 Только гифки и стикеры",
}


def _format_remaining(entry) -> str:
    from datetime import datetime
    from bot.domain.tz import TZ_MSK
    from bot.domain.bot_utils import format_duration
    secs = max(0, int((entry.expires_at - datetime.now(TZ_MSK)).total_seconds()))
    return format_duration(secs)


async def _cmd_mode(
    message: Message,
    command: CommandObject,
    service: ChatmodeService,
    config: AppConfig,
    pluralizer: ScorePluralizer,
    mode: str,
) -> None:
    if message.from_user is None or message.bot is None:
        return
    if message.chat.type not in ("group", "supergroup"):
        await reply_and_delete(message, "❌ Команда доступна только в групповых чатах.")
        return

    cfg = config.chatmode
    if not cfg.enabled:
        await reply_and_delete(message, "❌ Режимы чата отключены.")
        return

    mode_cfg = cfg.silence if mode == "silence" else cfg.gif

    # Парсим минуты из аргумента
    minutes = cfg.default_minutes
    if command.args:
        try:
            minutes = int(command.args.strip())
        except ValueError:
            await reply_and_delete(
                message,
                f"❌ Укажи количество минут числом. Например: /{mode} 10"
            )
            return

    if minutes < 1:
        await reply_and_delete(message, "❌ Минимум 1 минута.")
        return
    if minutes > mode_cfg.max_minutes:
        await reply_and_delete(
            message,
            f"❌ Максимальная длительность — {mode_cfg.max_minutes} мин."
        )
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
    )

    if not result.success:
        if result.error == "already_active":
            existing = await service.get_active(chat_id)
            if existing:
                who = user_link(None, "", existing.activated_by)
                remaining = _format_remaining(existing)
                label = _MODE_LABEL.get(existing.mode, existing.mode)
                await reply_and_delete(
                    message,
                    f"❌ Уже активен режим <b>{label}</b>.\n"
                    f"Осталось: <b>{remaining}</b>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await reply_and_delete(message, "❌ Режим уже активен.")
        elif result.error == "not_enough":
            score_word = pluralizer.pluralize(total_cost)
            await reply_and_delete(
                message,
                f"❌ Недостаточно кирчиков. Нужно <b>{total_cost} {score_word}</b>, "
                f"у тебя <b>{result.new_balance}</b>.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await reply_and_delete(message, f"❌ Ошибка: {result.error}")
        return

    # Успех
    label = _MODE_LABEL.get(mode, mode)
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
        f"{label} — режим активирован на <b>{minutes} мин.</b>\n\n"
        f"Инициатор: {initiator}\n"
        f"Списано: <b>{result.cost} {score_word}</b> "
        f"(осталось {result.new_balance} {balance_word})\n\n"
        f"Отключить раньше времени: /chatmode off",
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
) -> None:
    await _cmd_mode(message, command, service, config, pluralizer, "silence")


@router.message(Command("gif"))
@inject
async def cmd_gif(
    message: Message,
    command: CommandObject,
    service: FromDishka[ChatmodeService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    await _cmd_mode(message, command, service, config, pluralizer, "gif")


@router.message(Command("chatmode"))
@inject
async def cmd_chatmode(
    message: Message,
    command: CommandObject,
    service: FromDishka[ChatmodeService],
    config: FromDishka[AppConfig],
) -> None:
    """Управление активным режимом. Сейчас только: /chatmode off"""
    if message.from_user is None or message.bot is None:
        return
    if message.chat.type not in ("group", "supergroup"):
        await reply_and_delete(message, "❌ Команда доступна только в групповых чатах.")
        return

    arg = (command.args or "").strip().lower()
    if arg != "off":
        await reply_and_delete(
            message,
            "ℹ️ Использование:\n"
            "/chatmode off — отключить активный режим\n"
            "/silence [мин] — запретить всё\n"
            "/gif [мин] — только гифки и стикеры",
        )
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    is_cfg_admin = is_admin(message.from_user.username, config.admin.users)

    entry = await service.get_active(chat_id)
    if entry is None:
        await reply_and_delete(message, "ℹ️ Нет активного режима.")
        return

    # Отменить может только инициатор или админ конфига
    if entry.activated_by != user_id and not is_cfg_admin:
        # Проверяем Telegram-админа
        try:
            from aiogram.types import ChatMemberAdministrator, ChatMemberOwner
            member = await message.bot.get_chat_member(chat_id, user_id)
            if not isinstance(member, (ChatMemberAdministrator, ChatMemberOwner)):
                await reply_and_delete(
                    message,
                    "❌ Отменить режим может только его инициатор или администратор."
                )
                return
        except Exception:
            await reply_and_delete(message, "❌ Не удалось проверить права.")
            return

    await service.deactivate(message.bot, entry)

    label = _MODE_LABEL.get(entry.mode, entry.mode)
    try:
        await message.delete()
    except Exception:
        pass

    await message.bot.send_message(
        chat_id,
        f"✅ Режим <b>{label}</b> отключён досрочно.",
        parse_mode=ParseMode.HTML,
    )
