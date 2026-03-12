"""Обработчики команд /protect и /unprotect — защита от мута."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.mute_protection_repository import IMuteProtectionRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.utils import NO_PREVIEW

logger = logging.getLogger(__name__)
router = Router(name="protect")


@router.message(Command("protect"))
@inject
async def cmd_protect(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None:
        return
    mute_cfg = config.mute
    p = formatter._p
    chat_id = message.chat.id
    user_id = message.from_user.id
    cost = mute_cfg.protection_cost
    hours = mute_cfg.protection_duration_hours
    score = await score_service.get_score(user_id, chat_id)
    if score.value < cost:
        await message.reply(
            formatter._t["protect_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            )
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Да, потратить {cost} {p.pluralize(cost)}",
                    callback_data=f"protect:confirm:{user_id}:{chat_id}",
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"protect:cancel:{user_id}"),
            ]
        ]
    )
    await message.reply(
        formatter._t["protect_confirm"].format(
            hours=hours,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=score.value,
            score_word_balance=p.pluralize(score.value),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("protect:"))
@inject
async def cb_protect(
    callback: CallbackQuery,
    score_service: FromDishka[ScoreService],
    protection_repo: FromDishka[IMuteProtectionRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    async def safe_answer(text: str = "", alert: bool = False) -> None:
        try:
            await callback.answer(text, show_alert=alert)
        except Exception:
            pass

    parts = callback.data.split(":")
    action = parts[1]
    owner_id = int(parts[2])
    if callback.from_user.id != owner_id:
        await safe_answer("Это не твоя кнопка.", alert=True)
        return
    if action == "cancel":
        try:
            await callback.message.edit_text("❌ Защита отменена.")
        except Exception:
            pass
        await safe_answer()
        return
    chat_id = int(parts[3])
    mute_cfg = config.mute
    p = formatter._p
    cost = mute_cfg.protection_cost
    hours = mute_cfg.protection_duration_hours
    user_id = owner_id
    result = await score_service.spend_score(
        actor_id=user_id, target_id=user_id, chat_id=chat_id, cost=cost, emoji=SPECIAL_EMOJI["protect"]
    )
    if not result.success:
        try:
            await callback.message.edit_text(
                formatter._t["protect_not_enough"].format(
                    cost=cost,
                    score_word=p.pluralize(cost),
                    balance=result.current_balance,
                    score_word_balance=p.pluralize(result.current_balance),
                )
            )
        except Exception:
            pass
        await safe_answer()
        return
    existing = await protection_repo.get(user_id, chat_id)
    now = datetime.now(TZ_MSK)
    new_until = (existing if existing and existing > now else now) + timedelta(hours=hours)
    await protection_repo.save(user_id, chat_id, new_until)
    until_str = new_until.strftime("%H:%M %d.%m")
    user_link_str = user_link(callback.from_user.username, callback.from_user.full_name or "", user_id)
    if existing and existing > now:
        text = formatter._t["protect_extended"].format(
            until=until_str,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        )
    else:
        text = formatter._t["protect_success"].format(
            user=user_link_str,
            hours=hours,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        )
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await safe_answer()


@router.message(Command("unprotect"))
@inject
async def cmd_unprotect(
    message: Message,
    protection_repo: FromDishka[IMuteProtectionRepository],
) -> None:
    """Снять свою защиту от мута. Работает только для себя."""
    if message.from_user is None:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    existing = await protection_repo.get(user_id, chat_id)
    if existing is None:
        await message.reply("У тебя нет активной защиты.")
        return

    await protection_repo.delete(user_id, chat_id)
    await message.reply("🔓 Защита снята.")
