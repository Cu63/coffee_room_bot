"""Хендлер /poker — рулетка дебаффов."""

from __future__ import annotations

import logging
import math
import random
import time

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, ChatPermissions, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.mute_service import MuteService
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.entities import MuteEntry
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import (
    _ADMIN_PERM_FIELDS,
    _extract_admin_permissions,
)
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="poker")

# Типы дебаффов
DEBUFF_MUTE = "mute"
DEBUFF_GAMEBAN = "gameban"
DEBUFF_SCORE = "score"


async def _apply_mute_debuff(
    bot: Bot,
    target_id: int,
    chat_id: int,
    minutes: int,
    mute_service: MuteService,
    muted_by: int,
) -> bool:
    """Применить мут-дебафф. Возвращает True если мут наложен."""
    from datetime import datetime, timedelta

    now = datetime.now(TZ_MSK)
    until = now + timedelta(minutes=minutes)

    try:
        member = await bot.get_chat_member(chat_id, target_id)
    except Exception:
        return False

    if isinstance(member, ChatMemberOwner):
        return False

    was_admin = isinstance(member, ChatMemberAdministrator)
    admin_perms = None
    if was_admin:
        admin_perms = {}
        for field in _ADMIN_PERM_FIELDS:
            admin_perms[field] = getattr(member, field, False) or False
        if member.custom_title:
            admin_perms["custom_title"] = member.custom_title
        try:
            await bot.promote_chat_member(
                chat_id=chat_id, user_id=target_id, **{f: False for f in _ADMIN_PERM_FIELDS}
            )
        except Exception:
            return False

    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception:
        if was_admin and admin_perms:
            try:
                kw = {k: v for k, v in admin_perms.items() if k in _ADMIN_PERM_FIELDS}
                await bot.promote_chat_member(chat_id=chat_id, user_id=target_id, **kw)
            except Exception:
                pass
        return False

    await mute_service.save_mute(
        MuteEntry(
            user_id=target_id,
            chat_id=chat_id,
            muted_by=muted_by,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        )
    )
    return True


@router.message(Command("poker"))
@inject
async def cmd_poker(
    message: Message,
    config: FromDishka[AppConfig],
    formatter: FromDishka[MessageFormatter],
    score_service: FromDishka[ScoreService],
    score_repo: FromDishka[IScoreRepository],
    mute_service: FromDishka[MuteService],
    store: FromDishka[RedisStore],
) -> None:
    if message.from_user is None:
        return

    cfg = config.poker
    if not cfg.enabled:
        await reply_and_delete(message, formatter._t["poker_disabled"])
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    bot = message.bot
    p = formatter._p

    # Определяем цель: reply = другой игрок, без reply = на себя
    target_self = True
    target_id = user_id
    target_name = message.from_user.full_name or str(user_id)
    target_username = message.from_user.username

    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id != user_id:
        target_self = False
        target_id = reply.from_user.id
        target_name = reply.from_user.full_name or str(target_id)
        target_username = reply.from_user.username
        # Нельзя атаковать ботов
        if reply.from_user.is_bot:
            await reply_and_delete(
                message,
                formatter._t["poker_usage"].format(
                    cost=cfg.cost,
                    score_word=p.pluralize(cfg.cost),
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    # Проверяем баланс и списываем стоимость
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=cfg.cost,
        emoji=SPECIAL_EMOJI["poker"],
        bot_id=bot.id,
    )
    if not result.success:
        await reply_and_delete(
            message,
            formatter._t["poker_not_enough"].format(
                cost=cfg.cost,
                score_word=p.pluralize(cfg.cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ),
        )
        return

    actor_link = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
    target_link = user_link(target_username, target_name, target_id)

    # Шанс backfire при атаке другого
    actual_target_id = target_id
    actual_target_link = target_link
    backfire = False
    if not target_self and random.randint(1, 100) <= cfg.backfire_chance:
        backfire = True
        actual_target_id = user_id
        actual_target_link = actor_link

    # Выбираем случайный дебафф
    debuff = random.choice([DEBUFF_MUTE, DEBUFF_GAMEBAN, DEBUFF_SCORE])

    if backfire:
        backfire_text = formatter._t["poker_backfire"].format(
            actor=actor_link, target=target_link,
        )

    if debuff == DEBUFF_MUTE:
        success = await _apply_mute_debuff(
            bot, actual_target_id, chat_id, cfg.mute_minutes, mute_service, user_id,
        )
        if success:
            text = formatter._t["poker_mute"].format(
                actor=actor_link, target=actual_target_link, minutes=cfg.mute_minutes,
            )
        else:
            # Мут не удался — забираем баллы вместо мута
            debuff = DEBUFF_SCORE

    if debuff == DEBUFF_GAMEBAN:
        until_ts = time.time() + cfg.gameban_minutes * 60
        await store.gameban_set(actual_target_id, chat_id, until_ts)
        text = formatter._t["poker_gameban"].format(
            actor=actor_link, target=actual_target_link, minutes=cfg.gameban_minutes,
        )

    if debuff == DEBUFF_SCORE:
        if target_self:
            # На себя: теряешь 5% своего баланса, деньги идут боту
            score = await score_service.get_score(actual_target_id, chat_id)
            amount = max(1, math.ceil(score.value * cfg.score_percent / 100))
            if score.value > 0:
                await score_repo.add_delta(actual_target_id, chat_id, -amount)
                await score_repo.add_delta(bot.id, chat_id, amount)
                text = formatter._t["poker_score"].format(
                    actor=actor_link, target=actual_target_link,
                    amount=amount, score_word=p.pluralize(amount),
                )
            else:
                text = formatter._t["poker_score_zero"].format(
                    actor=actor_link, target=actual_target_link,
                )
        else:
            # На другого (или backfire): крадём деньги (макс 5% от баланса атакующего)
            attacker_score = await score_service.get_score(user_id, chat_id)
            max_steal = max(1, math.ceil(attacker_score.value * cfg.steal_percent / 100))
            victim_score = await score_service.get_score(actual_target_id, chat_id)

            if victim_score.value > 0:
                amount = min(max_steal, victim_score.value)
                await score_repo.add_delta(actual_target_id, chat_id, -amount)
                if backfire:
                    # При backfire деньги идут боту
                    await score_repo.add_delta(bot.id, chat_id, amount)
                    text = formatter._t["poker_score"].format(
                        actor=actor_link, target=actual_target_link,
                        amount=amount, score_word=p.pluralize(amount),
                    )
                else:
                    # Крадём деньги жертвы себе
                    await score_repo.add_delta(user_id, chat_id, amount)
                    text = formatter._t["poker_steal"].format(
                        actor=actor_link, target=actual_target_link,
                        amount=amount, score_word=p.pluralize(amount),
                    )
            else:
                text = formatter._t["poker_score_zero"].format(
                    actor=actor_link, target=actual_target_link,
                )

    final_text = f"{backfire_text}\n{text}" if backfire else text

    await reply_and_delete(
        message,
        final_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        delay=120,
    )
