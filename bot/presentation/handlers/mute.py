"""Обработчики команд мута: /mute, /amute, /selfmute, /unmute."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberOwner,
    ChatPermissions,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.mute_protection_repository import IMuteProtectionRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.application.score_service import ScoreService
from bot.domain.bot_utils import format_duration, is_admin, parse_duration
from bot.domain.entities import MuteEntry
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import (
    _ADMIN_PERM_FIELDS,
    _extract_admin_permissions,
    _promote_kwargs,
    _resolve_mute_args,
    _resolve_username,
    _unmute_user,
)
from bot.presentation.utils import NO_PREVIEW

logger = logging.getLogger(__name__)
router = Router(name="mute")


@router.message(Command("mute"))
@inject
async def cmd_mute(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    mute_service: FromDishka[MuteService],
    protection_repo: FromDishka[IMuteProtectionRepository],
    user_repo: FromDishka[IUserRepository],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    mute_cfg = config.mute
    p = formatter._p
    parsed = await _resolve_mute_args(command.args, message, user_repo)
    if parsed is None:
        await message.reply(formatter._t["mute_usage"].format(min=mute_cfg.min_minutes, max=mute_cfg.max_minutes))
        return
    target, minutes = parsed
    if target is None:
        await message.reply(formatter._t["error_user_not_found"])
        return
    if target.id == message.from_user.id:
        await message.reply(formatter._t["mute_self"])
        return
    if minutes < mute_cfg.min_minutes or minutes > mute_cfg.max_minutes:
        await message.reply(
            formatter._t["mute_invalid_minutes"].format(min=mute_cfg.min_minutes, max=mute_cfg.max_minutes)
        )
        return
    # Дневной лимит мутов
    if mute_cfg.daily_limit > 0:
        daily_count = await store.mute_daily_count(message.from_user.id, message.chat.id)
        if daily_count >= mute_cfg.daily_limit:
            await message.reply(
                formatter._t["mute_daily_limit"].format(count=daily_count, limit=mute_cfg.daily_limit)
            )
            return
    # Кулдаун между мутами одного участника
    target_link = user_link(target.username, target.full_name, target.id)
    if mute_cfg.target_cooldown_hours > 0:
        if not await store.mute_target_cooldown_ok(message.from_user.id, target.id, message.chat.id):
            await message.reply(
                formatter._t["mute_target_cooldown"].format(
                    target=target_link, hours=mute_cfg.target_cooldown_hours
                ),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
    protected_until = await protection_repo.get(target.id, message.chat.id)
    if protected_until is not None:
        until_str = protected_until.astimezone(TZ_MSK).strftime("%H:%M %d.%m")
        await message.reply(
            formatter._t["mute_target_protected"].format(target=target_link, until=until_str),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    cost = minutes * mute_cfg.cost_per_minute
    score = await score_service.get_score(message.from_user.id, message.chat.id)
    if score.value < cost:
        await message.reply(
            formatter._t["mute_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            )
        )
        return
    bot = message.bot
    chat_id = message.chat.id
    until = datetime.now(TZ_MSK) + timedelta(minutes=minutes)
    try:
        member = await bot.get_chat_member(chat_id, target.id)
    except Exception:
        await message.reply(formatter._t["mute_failed"])
        return
    was_admin = isinstance(member, ChatMemberAdministrator)
    admin_perms: dict | None = None
    if was_admin:
        admin_perms = _extract_admin_permissions(member)
        try:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=target.id, **{f: False for f in _ADMIN_PERM_FIELDS}
            )
        except Exception:
            await message.reply(formatter._t["mute_failed"])
            return
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception:
        if was_admin and admin_perms:
            try:
                await bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **_promote_kwargs(admin_perms))
            except Exception:
                logger.exception("Failed to restore admin rights after mute failure")
        await message.reply(formatter._t["mute_failed"])
        return
    await mute_service.save_mute(
        MuteEntry(
            user_id=target.id,
            chat_id=chat_id,
            muted_by=message.from_user.id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        )
    )
    result = await score_service.spend_score(
        actor_id=message.from_user.id, target_id=target.id, chat_id=chat_id, cost=cost
    )
    if not result.success:
        await _unmute_user(
            bot,
            mute_service,
            MuteEntry(
                user_id=target.id,
                chat_id=chat_id,
                muted_by=message.from_user.id,
                until_at=until,
                was_admin=was_admin,
                admin_permissions=admin_perms,
            ),
        )
        await message.reply(
            formatter._t["mute_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            )
        )
        return
    # Фиксируем мут в Redis (счётчик и кулдаун)
    if mute_cfg.daily_limit > 0:
        await store.mute_daily_increment(message.from_user.id, chat_id)
    if mute_cfg.target_cooldown_hours > 0:
        await store.mute_target_cooldown_set(message.from_user.id, target.id, chat_id, mute_cfg.target_cooldown_hours)
    actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
    await message.reply(
        formatter._t["mute_success"].format(
            actor=actor_link,
            target=target_link,
            minutes=minutes,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        ),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("amute"))
@inject
async def cmd_amute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Бесплатный мут для администраторов, обходит /protect."""
    if message.from_user is None or message.bot is None:
        return
    bot = message.bot
    chat_id = message.chat.id
    mute_cfg = config.mute
    is_config_admin = is_admin(message.from_user.username, config.admin.users)
    if not is_config_admin:
        try:
            caller_member = await bot.get_chat_member(chat_id, message.from_user.id)
            has_restrict = (
                isinstance(caller_member, ChatMemberAdministrator) and caller_member.can_restrict_members
            ) or isinstance(caller_member, ChatMemberOwner)
        except Exception:
            has_restrict = False
        if not has_restrict:
            await message.reply(formatter._t["amute_not_allowed"])
            return
    parsed = await _resolve_mute_args(command.args, message, user_repo)
    if parsed is None:
        await message.reply(formatter._t["amute_usage"].format(min=mute_cfg.min_minutes, max=mute_cfg.max_minutes))
        return
    target, minutes = parsed
    if target is None:
        await message.reply(formatter._t["error_user_not_found"])
        return
    if target.id == message.from_user.id:
        await message.reply(formatter._t["mute_self"])
        return
    until = datetime.now(TZ_MSK) + timedelta(minutes=minutes)
    try:
        member = await bot.get_chat_member(chat_id, target.id)
    except Exception:
        await message.reply(formatter._t["mute_failed"])
        return
    was_admin = isinstance(member, ChatMemberAdministrator)
    admin_perms: dict | None = None
    if was_admin:
        admin_perms = _extract_admin_permissions(member)
        try:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=target.id, **{f: False for f in _ADMIN_PERM_FIELDS}
            )
        except Exception:
            await message.reply(formatter._t["mute_failed"])
            return
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception:
        if was_admin and admin_perms:
            try:
                await bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **_promote_kwargs(admin_perms))
            except Exception:
                logger.exception("Failed to restore admin rights after amute failure")
        await message.reply(formatter._t["mute_failed"])
        return
    await mute_service.save_mute(
        MuteEntry(
            user_id=target.id,
            chat_id=chat_id,
            muted_by=message.from_user.id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        )
    )
    actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
    target_link = user_link(target.username, target.full_name, target.id)
    await message.reply(
        formatter._t["amute_success"].format(actor=actor_link, target=target_link, minutes=minutes),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("selfmute"))
@inject
async def cmd_selfmute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    mute_cfg = config.mute
    min_sec = mute_cfg.selfmute_min_minutes * 60
    max_sec = mute_cfg.selfmute_max_minutes * 60
    if not command.args:
        await message.reply(
            formatter._t["selfmute_usage"].format(
                min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes
            )
        )
        return
    seconds = parse_duration(command.args)
    if seconds is None or seconds <= 0 or seconds < min_sec or seconds > max_sec:
        await message.reply(
            formatter._t["selfmute_invalid_minutes"].format(
                min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes
            )
        )
        return
    bot = message.bot
    chat_id = message.chat.id
    user_id = message.from_user.id
    until = datetime.now(TZ_MSK) + timedelta(seconds=seconds)
    was_admin = False
    admin_perms: dict | None = None
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if isinstance(member, ChatMemberOwner):
            await message.reply(formatter._t["selfmute_failed"])
            return
        if isinstance(member, ChatMemberAdministrator):
            was_admin = True
            admin_perms = _extract_admin_permissions(member)
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=user_id, **{f: False for f in _ADMIN_PERM_FIELDS}
            )
    except TelegramBadRequest as e:
        logger.warning("selfmute pre-check failed for user %d: %s", user_id, e)
        await message.reply(formatter._t["selfmute_failed"])
        return
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False), until_date=until
        )
    except Exception:
        if was_admin and admin_perms:
            try:
                await bot.promote_chat_member(chat_id=chat_id, user_id=user_id, **_promote_kwargs(admin_perms))
            except Exception:
                logger.exception("Failed to restore admin rights after selfmute failure for user %d", user_id)
        await message.reply(formatter._t["selfmute_failed"])
        return
    await mute_service.save_mute(
        MuteEntry(
            user_id=user_id,
            chat_id=chat_id,
            muted_by=user_id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        )
    )
    user_link_str = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
    await message.reply(
        formatter._t["selfmute_success"].format(user=user_link_str, duration=format_duration(seconds)),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("unmute"))
@inject
async def cmd_unmute(
    message: Message,
    command: CommandObject,
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None or message.bot is None:
        return
    if not is_admin(message.from_user.username, config.admin.users):
        await message.reply(formatter._t["admin_not_allowed"])
        return
    target = await _resolve_username(command.args, user_repo)
    if target is None:
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None:
            from bot.domain.entities import User as DomainUser

            tg = reply.from_user
            target = DomainUser(id=tg.id, username=tg.username, full_name=tg.full_name or str(tg.id))
        else:
            await message.reply(formatter._t["unmute_usage"])
            return
    display = user_link(target.username, target.full_name, target.id)
    entry = await mute_service._repo.get(target.id, message.chat.id)
    if entry is None:
        await message.reply(
            formatter._t["unmute_not_muted"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    await _unmute_user(message.bot, mute_service, entry)
    await message.reply(
        formatter._t["unmute_success"].format(user=display),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
