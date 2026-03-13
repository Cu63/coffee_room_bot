"""Обработчик команды /tag — смена тега участника чата."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.bot_utils import is_admin
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="tag")


@router.message(Command("tag"))
@inject
async def cmd_tag(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    tc = config.tag
    p = formatter._p
    bot = message.bot
    chat_id = message.chat.id
    args = command.args
    if not args:
        await reply_and_delete(message,
            formatter._t["tag_usage"].format(cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self))
        )
        return
    parts = args.strip().split(maxsplit=1)
    if parts[0].startswith("@"):
        target = await user_repo.get_by_username(parts[0].lstrip("@"))
        if target is None:
            await reply_and_delete(message,formatter._t["error_user_not_found"])
            return
        new_tag = parts[1].strip() if len(parts) > 1 else None
        if new_tag is None:
            await reply_and_delete(message,
                formatter._t["tag_usage"].format(cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self))
            )
            return
        is_self = target.id == message.from_user.id
    else:
        target = await user_repo.get_by_id(message.from_user.id)
        if target is None:
            await reply_and_delete(message,formatter._t["error_user_not_found"])
            return
        new_tag = args.strip()
        is_self = True
    clearing = new_tag == "--clear"
    is_free = is_admin(message.from_user.username, config.admin.users)
    if not clearing and len(new_tag) > tc.max_length:
        await reply_and_delete(message,formatter._t["tag_too_long"].format(max=tc.max_length))
        return
    if is_self:
        cost = tc.cost_self
    else:
        try:
            member = await bot.get_chat_member(chat_id, target.id)
        except Exception:
            await reply_and_delete(message,formatter._t["tag_failed"])
            return
        if isinstance(member, ChatMemberOwner):
            cost = tc.cost_owner
        elif isinstance(member, ChatMemberAdministrator):
            cost = tc.cost_admin
        else:
            cost = tc.cost_member
    if not is_free:
        score = await score_service.get_score(message.from_user.id, chat_id)
        if score.value < cost:
            await reply_and_delete(message,
                formatter._t["tag_not_enough"].format(
                    cost=cost,
                    score_word=p.pluralize(cost),
                    balance=score.value,
                    score_word_balance=p.pluralize(score.value),
                )
            )
            return
    try:
        await bot.set_chat_member_tag(chat_id=chat_id, user_id=target.id, tag=None if clearing else new_tag)
    except Exception:
        await reply_and_delete(message,formatter._t["tag_failed"])
        return
    target_link = user_link(target.username, target.full_name, target.id)
    if is_free:
        text = (
            formatter._t["tag_cleared_free"].format(target=target_link)
            if clearing
            else formatter._t["tag_success_free"].format(target=target_link, tag=new_tag)
        )
        await reply_and_delete(message,text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        return
    result = await score_service.spend_score(
        actor_id=message.from_user.id, target_id=target.id, chat_id=chat_id, cost=cost, emoji=SPECIAL_EMOJI["tag"]
    )
    if not result.success:
        await reply_and_delete(message,
            formatter._t["tag_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            )
        )
        return
    if clearing:
        text = formatter._t["tag_cleared"].format(
            target=target_link,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        )
    else:
        text = formatter._t["tag_success"].format(
            target=target_link,
            tag=new_tag,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        )
    await reply_and_delete(message,text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
