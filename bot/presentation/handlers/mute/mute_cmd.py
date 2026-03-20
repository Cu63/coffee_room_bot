"""Команда /mute — мут пользователя за баллы."""

from __future__ import annotations

import logging
import math
from datetime import datetime

from aiogram import Router
from aiogram.enums import ParseMode
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
    _unmute_user,
)
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="mute_cmd")


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
        await reply_and_delete(message, formatter._t["mute_usage"].format(min=mute_cfg.min_minutes, max=mute_cfg.max_minutes))
        return
    target, minutes = parsed
    if target is None:
        await reply_and_delete(message, formatter._t["error_user_not_found"])
        return
    if target.id == message.from_user.id:
        await reply_and_delete(message, formatter._t["mute_self"])
        return
    if minutes < mute_cfg.min_minutes or minutes > mute_cfg.max_minutes:
        await reply_and_delete(
            message,
            formatter._t["mute_invalid_minutes"].format(min=mute_cfg.min_minutes, max=mute_cfg.max_minutes),
        )
        return
    # Дневной лимит мутов
    if mute_cfg.daily_limit > 0:
        daily_count = await store.mute_daily_count(message.from_user.id, message.chat.id)
        if daily_count >= mute_cfg.daily_limit:
            await reply_and_delete(
                message,
                formatter._t["mute_daily_limit"].format(count=daily_count, limit=mute_cfg.daily_limit),
            )
            return
    # Кулдаун между мутами одного участника
    target_link = user_link(target.username, target.full_name, target.id)
    if mute_cfg.target_cooldown_hours > 0:
        if not await store.mute_target_cooldown_ok(message.from_user.id, target.id, message.chat.id):
            await reply_and_delete(
                message,
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
        await reply_and_delete(
            message,
            formatter._t["mute_target_protected"].format(target=target_link, until=until_str),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return
    cost = minutes * mute_cfg.cost_per_minute
    score = await score_service.get_score(message.from_user.id, message.chat.id)
    if score.value < cost:
        await reply_and_delete(
            message,
            formatter._t["mute_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ),
        )
        return
    bot = message.bot
    chat_id = message.chat.id
    # Стекуем: прибавляем к оставшемуся времени мута, не заменяем его
    until, mute_was_stacked = await mute_service.compute_stacked_until(
        target.id, chat_id, minutes * 60
    )

    # Проверяем, является ли цель владельцем чата
    try:
        member = await bot.get_chat_member(chat_id, target.id)
    except Exception:
        await reply_and_delete(message, formatter._t["mute_failed"])
        return

    if isinstance(member, ChatMemberOwner):
        # ── Owner soft-mute: удаляем сообщения через middleware ──
        result = await score_service.spend_score(
            actor_id=message.from_user.id, target_id=target.id, chat_id=chat_id, cost=cost
        )
        if not result.success:
            await reply_and_delete(
                message,
                formatter._t["mute_not_enough"].format(
                    cost=cost,
                    score_word=p.pluralize(cost),
                    balance=result.current_balance,
                    score_word_balance=p.pluralize(result.current_balance),
                ),
            )
            return
        await store.owner_mute_set(chat_id, target.id, until.timestamp())
        await mute_service.log_mute(target.id, message.from_user.id, chat_id)
        # Фиксируем в Redis
        if mute_cfg.daily_limit > 0:
            await store.mute_daily_increment(message.from_user.id, chat_id)
        if mute_cfg.target_cooldown_hours > 0:
            await store.mute_target_cooldown_set(message.from_user.id, target.id, chat_id, mute_cfg.target_cooldown_hours)
        actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
        total_minutes = math.ceil((until - datetime.now(TZ_MSK)).total_seconds() / 60)
        stack_note = f" (итого: {total_minutes} мин)" if mute_was_stacked else ""
        await reply_and_delete(
            message,
            formatter._t["mute_success"].format(
                actor=actor_link,
                target=target_link,
                minutes=minutes,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.new_balance,
                score_word_balance=p.pluralize(result.new_balance),
            ) + stack_note,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    # ── Обычный мут через Telegram restrict ──────────────────────
    was_admin = isinstance(member, ChatMemberAdministrator)
    admin_perms: dict | None = None
    if was_admin:
        admin_perms = _extract_admin_permissions(member)
        try:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=target.id, **{f: False for f in _ADMIN_PERM_FIELDS}
            )
        except Exception:
            await reply_and_delete(message, formatter._t["mute_failed"])
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
        await reply_and_delete(message, formatter._t["mute_failed"])
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
        await reply_and_delete(
            message,
            formatter._t["mute_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ),
        )
        return
    # Фиксируем мут в Redis (счётчик и кулдаун)
    if mute_cfg.daily_limit > 0:
        await store.mute_daily_increment(message.from_user.id, chat_id)
    if mute_cfg.target_cooldown_hours > 0:
        await store.mute_target_cooldown_set(message.from_user.id, target.id, chat_id, mute_cfg.target_cooldown_hours)
    await mute_service.log_mute(target.id, message.from_user.id, chat_id)
    actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
    total_minutes = math.ceil((until - datetime.now(TZ_MSK)).total_seconds() / 60)
    stack_note = f" (итого: {total_minutes} мин)" if mute_was_stacked else ""
    await reply_and_delete(
        message,
        formatter._t["mute_success"].format(
            actor=actor_link,
            target=target_link,
            minutes=minutes,
            cost=cost,
            score_word=p.pluralize(cost),
            balance=result.new_balance,
            score_word_balance=p.pluralize(result.new_balance),
        ) + stack_note,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
