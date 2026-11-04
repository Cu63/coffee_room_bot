"""Обработчик команды /tag — смена тега участника чата."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.bot_utils import is_admin
from bot.domain.ledger import LedgerKind
from bot.infrastructure.config_loader import AppConfig, TagConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.errors import AbortUpdate
from bot.presentation.handlers._admin_utils import _extract_admin_permissions
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="tag")


_TAG_CD_PREFIX = "tag:cd:"


@dataclass(slots=True, frozen=True)
class _TagPlan:
    cost: int = 0
    as_title: bool = False  # тег админа — это его титул
    refusal: str | None = None  # текст отказа: этот тег боту не поменять


async def _plan(
    bot: Bot, mute_service: MuteService, chat_id: int, target_id: int, is_self: bool, tc: TagConfig
) -> _TagPlan:
    """Как и за сколько поменять тег.

    Тег участника Telegram ставит только обычным участникам, а титул админа бот может менять
    лишь тем, кого назначил сам; владельцу — никак. Цена: себе, админу (может удалять сообщения
    и банить) или участнику. Мут бота снимает с админа админку, и для Telegram он обычный
    участник — его права тогда берутся из снимка, который сохранил мут.
    """
    member = await bot.get_chat_member(chat_id, target_id)
    if isinstance(member, ChatMemberOwner):
        return _TagPlan(refusal="tag_owner_not_editable")
    as_title = isinstance(member, ChatMemberAdministrator)
    if as_title:
        if not member.can_be_edited:
            return _TagPlan(refusal="tag_admin_not_editable")
        rights = _extract_admin_permissions(member)
    else:
        muted = await mute_service.get_mute(target_id, chat_id)
        rights = muted.admin_permissions if muted is not None and muted.was_admin else None
    if is_self:
        cost = tc.cost_self
    elif rights and rights.get("can_delete_messages") and rights.get("can_restrict_members"):
        cost = tc.cost_admin
    else:
        cost = tc.cost_member
    return _TagPlan(cost=cost, as_title=as_title)


@router.message(Command("tag"))
@inject
async def cmd_tag(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
    store: FromDishka[RedisStore],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    tc = config.tag
    p = formatter._p
    bot = message.bot
    chat_id = message.chat.id
    args = command.args
    if not args:
        await reply_and_delete(
            message, formatter._t["tag_usage"].format(cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self))
        )
        return
    parts = args.strip().split(maxsplit=1)
    if parts[0].startswith("@"):
        target = await user_repo.get_by_username(parts[0].lstrip("@"))
        if target is None:
            await reply_and_delete(message, formatter._t["error_user_not_found"])
            return
        new_tag = parts[1].strip() if len(parts) > 1 else None
        if new_tag is None:
            await reply_and_delete(
                message, formatter._t["tag_usage"].format(cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self))
            )
            return
        is_self = target.id == message.from_user.id
    else:
        target = await user_repo.get_by_id(message.from_user.id)
        if target is None:
            await reply_and_delete(message, formatter._t["error_user_not_found"])
            return
        new_tag = args.strip()
        is_self = True
    clearing = new_tag == "--clear"
    is_free = is_admin(message.from_user, config.admin.users)

    if not is_free and tc.cooldown_hours > 0:
        cd_key = f"{_TAG_CD_PREFIX}{chat_id}:{target.id}"
        ttl = await store._r.ttl(cd_key)
        if ttl > 0:
            hours = ttl // 3600
            minutes = (ttl % 3600) // 60
            if hours > 0:
                await reply_and_delete(
                    message, f"⏳ Тег можно менять раз в {tc.cooldown_hours}ч. Осталось: {hours}ч {minutes}мин."
                )
            else:
                await reply_and_delete(
                    message, f"⏳ Тег можно менять раз в {tc.cooldown_hours}ч. Осталось: {minutes}мин."
                )
            return

    if not clearing and len(new_tag) > tc.max_length:
        await reply_and_delete(message, formatter._t["tag_too_long"].format(max=tc.max_length))
        return
    try:
        plan = await _plan(bot, mute_service, chat_id, target.id, is_self, tc)
    except TelegramAPIError:
        await reply_and_delete(message, formatter._t["tag_failed"])
        return
    if plan.refusal is not None:
        await reply_and_delete(
            message,
            formatter._t[plan.refusal].format(target=user_link(target.username, target.full_name, target.id)),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    cost = plan.cost

    # Сначала оплата (атомарно), потом тег: раньше тег ставился до списания и при неудачном
    # списании оставался бесплатным
    paid = None
    if not is_free:
        paid = await score_service.spend_score(
            actor_id=message.from_user.id,
            target_id=target.id,
            chat_id=chat_id,
            cost=cost,
            emoji=SPECIAL_EMOJI["tag"],
            kind=LedgerKind.PURCHASE,
            bot_id=bot.id,
        )
        if not paid.success:
            await reply_and_delete(
                message,
                formatter._t["tag_not_enough"].format(
                    cost=cost,
                    score_word=p.pluralize(cost),
                    balance=paid.current_balance,
                    score_word_balance=p.pluralize(paid.current_balance),
                ),
            )
            return

    tag = None if clearing else new_tag
    # Замученному админу тег вернётся титулом при размуте, а не прежний титул из снимка
    await mute_service.set_admin_title(target.id, chat_id, tag)
    try:
        if plan.as_title:
            await bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=target.id, custom_title=tag or "")
        else:
            await bot.set_chat_member_tag(chat_id=chat_id, user_id=target.id, tag=tag)
    except TelegramAPIError:
        await reply_and_delete(message, formatter._t["tag_failed"])
        raise AbortUpdate  # откатывает оплату и титул в снимке мута

    if not is_free and tc.cooldown_hours > 0:
        await store._r.set(f"{_TAG_CD_PREFIX}{chat_id}:{target.id}", "1", ex=tc.cooldown_hours * 3600)

    target_link = user_link(target.username, target.full_name, target.id)
    if paid is None:
        text = (
            formatter._t["tag_cleared_free"].format(target=target_link)
            if clearing
            else formatter._t["tag_success_free"].format(target=target_link, tag=html.escape(new_tag))
        )
    elif clearing:
        text = formatter._t["tag_cleared"].format(
            target=target_link,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=paid.new_balance,
            score_word_balance=p.pluralize(paid.new_balance),
        )
    else:
        text = formatter._t["tag_success"].format(
            target=target_link,
            tag=html.escape(new_tag),
            cost=cost,
            score_word=p.pluralize(cost),
            balance=paid.new_balance,
            score_word_balance=p.pluralize(paid.new_balance),
        )
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
