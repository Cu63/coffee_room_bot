"""Хендлер /buyop — покупка титула админа без реальных прав."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="buyop")


async def _count_bot_admins(bot, chat_id: int) -> int:
    """Считает количество админов, назначенных ботом (can_manage_chat=True и минимальные права)."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception:
        return 0
    count = 0
    for m in admins:
        if isinstance(m, ChatMemberOwner):
            continue
        if not isinstance(m, ChatMemberAdministrator):
            continue
        if m.user.is_bot:
            continue
        if m.can_manage_chat and not m.can_restrict_members and not m.can_delete_messages:
            count += 1
    return count


@router.message(Command("buyop"))
@inject
async def cmd_buyop(
    message: Message,
    score_service: FromDishka[ScoreService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return

    bot = message.bot
    chat_id = message.chat.id
    user_id = message.from_user.id
    p = formatter._p
    display = user_link(message.from_user.username, message.from_user.full_name or "", user_id)

    # Проверяем: уже админ?
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        await reply_and_delete(message, formatter._t["buyop_failed"])
        return

    if isinstance(member, (ChatMemberOwner, ChatMemberAdministrator)):
        await reply_and_delete(
            message,
            formatter._t["buyop_already_admin"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # Динамическая цена: base + step * кол-во купленных админов
    bought_admins = await _count_bot_admins(bot, chat_id)
    cost = config.buyop.cost + config.buyop.cost_step * bought_admins

    # Проверяем баланс
    score = await score_service.get_score(user_id, chat_id)
    if score.value < cost:
        await reply_and_delete(
            message,
            formatter._t["buyop_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ),
        )
        return

    # Promote без реальных прав (все False) — Telegram создаст запись
    # админа с нулевыми правами, что равнозначно «титулу»
    try:
        await bot.promote_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            can_manage_chat=True,  # минимальное право для отображения в списке админов
        )
        tag = config.buyop.tag
        if tag:
            await bot.set_chat_administrator_custom_title(
                chat_id=chat_id, user_id=user_id, custom_title=tag,
            )
    except Exception:
        logger.exception("Failed to buyop user %d", user_id)
        await reply_and_delete(message, formatter._t["buyop_failed"])
        return

    # Списываем кирчики
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=cost,
        emoji=SPECIAL_EMOJI.get("buyop", "🎖"),
        bot_id=message.bot.id,
    )

    if not result.success:
        # Откатываем promote
        try:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=user_id, can_manage_chat=False,
            )
        except Exception:
            pass
        await reply_and_delete(
            message,
            formatter._t["buyop_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ),
        )
        return

    await reply_and_delete(
        message,
        formatter._t["buyop_success"].format(
            user=display,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
