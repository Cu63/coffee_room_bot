from __future__ import annotations

import html
import logging
import random
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.mute_service import MuteService
from bot.domain.bot_utils import is_admin, parse_duration
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import MuteOutcome, apply_mute
from bot.presentation.utils import callback_message, reply_and_delete, schedule_delete, schedule_delete_id

logger = logging.getLogger(__name__)

router = Router(name="giveaway_mute_roulette")


# ─── Мут-рулетка ────────────────────────────────────────────────────────────


def _mute_roulette_kb(chat_id: int, roulette_id: str, count: int, t: dict[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t["mute_roulette_button"].format(count=count),
                    callback_data=f"mutegiveaway:join:{chat_id}:{roulette_id}",
                )
            ]
        ]
    )


@router.message(Command("mutegiveaway"))
@inject
async def cmd_mute_roulette(
    message: Message,
    bot: Bot,
    config: FromDishka[AppConfig],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
) -> None:
    t = formatter._t
    if message.from_user is None or not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["mute_roulette_admins_only"])
        return

    args = (message.text or "").split()[1:]
    # /mutegiveaway <время_мута> <кол-во_проигравших> <время_сбора>
    # /mutegiveaway 10m 2 5m
    if len(args) < 3:
        await reply_and_delete(message, t["mute_roulette_usage"], parse_mode="HTML")
        return

    mute_secs = parse_duration(args[0])
    if mute_secs is None or mute_secs < 60:
        await reply_and_delete(message, t["mute_roulette_bad_mute"])
        return

    try:
        losers_count = int(args[1])
        if losers_count < 1:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, t["mute_roulette_bad_losers"])
        return

    collect_secs = parse_duration(args[2])
    if collect_secs is None or collect_secs < 30:
        await reply_and_delete(message, t["mute_roulette_bad_collect"])
        return

    chat_id = message.chat.id
    import time

    ends_at = time.time() + collect_secs
    mute_minutes = mute_secs // 60

    roulette_id = await store.mute_roulette_create(
        chat_id=chat_id,
        creator_id=message.from_user.id,
        mute_minutes=mute_minutes,
        losers_count=losers_count,
        ends_at=ends_at,
    )

    if collect_secs >= 60:
        collect_str = t["giveaway_unit_minutes"].format(n=collect_secs // 60)
    else:
        collect_str = t["giveaway_unit_seconds"].format(n=collect_secs)
    text = t["mute_roulette_started"].format(
        minutes=mute_minutes, losers=losers_count, collect=collect_str, id=roulette_id
    )
    sent = await message.answer(text, parse_mode="HTML", reply_markup=_mute_roulette_kb(chat_id, roulette_id, 0, t))
    await store.mute_roulette_set_message_id(chat_id, roulette_id, sent.message_id)


@router.callback_query(F.data.startswith("mutegiveaway:join:"))
@inject
async def cb_mute_roulette_join(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if cb.data is None:
        await cb.answer()
        return
    parts = cb.data.split(":")
    chat_id = int(parts[2])
    roulette_id = parts[3]
    user_id = cb.from_user.id

    joined = await store.mute_roulette_join(chat_id, roulette_id, user_id)
    if not joined:
        await cb.answer(formatter._t["mute_roulette_already_joined"], show_alert=False)
        return

    count = await store.mute_roulette_count(chat_id, roulette_id)
    await cb.answer(formatter._t["mute_roulette_joined"], show_alert=False)

    message = callback_message(cb)
    if message is not None:
        try:
            await message.edit_reply_markup(reply_markup=_mute_roulette_kb(chat_id, roulette_id, count, formatter._t))
        except Exception:
            pass


@router.message(Command("mutegiveaway_end"))
@inject
async def cmd_mute_roulette_end(
    message: Message,
    bot: Bot,
    config: FromDishka[AppConfig],
    store: FromDishka[RedisStore],
    mute_service: FromDishka[MuteService],
    formatter: FromDishka[MessageFormatter],
) -> None:
    t = formatter._t
    if message.from_user is None or not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["mute_roulette_end_admins_only"])
        return

    chat_id = message.chat.id
    args = (message.text or "").split()[1:]

    roulette_id: str | None = args[0] if args else None

    if roulette_id is None:
        active = await store.mute_roulette_list(chat_id)
        if not active:
            await reply_and_delete(message, t["mute_roulette_none"])
            return
        if len(active) == 1:
            roulette_id = active[0][0]
        else:
            lines = [t["mute_roulette_choose"]]
            for rid, data in active:
                lines.append(
                    t["mute_roulette_choose_row"].format(
                        id=rid,
                        minutes=data["mute_minutes"],
                        losers=data["losers_count"],
                        participants=len(data["participants"]),
                    )
                )
            await reply_and_delete(message, "\n".join(lines), parse_mode="HTML")
            return

    data = await store.mute_roulette_delete(chat_id, roulette_id)
    if data is None:
        await reply_and_delete(message, t["mute_roulette_not_found"])
        return

    await _finish_mute_roulette(bot, chat_id, data, mute_service, t)


async def _finish_mute_roulette(
    bot: Bot,
    chat_id: int,
    data: dict,
    mute_service: MuteService,
    t: dict[str, str],
) -> None:
    """Завершение мут-гивэвея: выбор проигравших и применение мутов."""
    participants = data["participants"]
    losers_count = data["losers_count"]
    mute_minutes = data["mute_minutes"]
    creator_id = data["creator_id"]

    lobby_message_id: int = data.get("message_id", 0)

    if not participants:
        result_msg = await bot.send_message(chat_id, t["mute_roulette_no_participants"])
        schedule_delete(bot, result_msg, delay=30)
        if lobby_message_id:
            schedule_delete_id(bot, chat_id, lobby_message_id, delay=30)
        return

    losers = random.sample(participants, min(losers_count, len(participants)))
    until = datetime.now(TZ_MSK) + timedelta(minutes=mute_minutes)

    lines = [t["mute_roulette_results"].format(count=len(participants))]
    for user_id in losers:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            name = f'<a href="tg://user?id={user_id}">{html.escape(member.user.full_name)}</a>'
        except Exception:
            name = f"<code>{user_id}</code>"
            member = None

        muted = await apply_mute(
            bot, mute_service, target_id=user_id, chat_id=chat_id, muted_by=creator_id, until=until
        )
        if muted.outcome == MuteOutcome.MUTED:
            lines.append(t["mute_roulette_muted"].format(name=name, minutes=mute_minutes))
        elif muted.outcome == MuteOutcome.OWNER:
            lines.append(t["mute_roulette_owner"].format(name=name))
        elif muted.outcome == MuteOutcome.RESTRICTED_BY_ADMIN:
            lines.append(t["mute_roulette_restricted"].format(name=name))
        else:
            lines.append(t["mute_roulette_failed"].format(name=name))

    result_msg = await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    schedule_delete(bot, result_msg, delay=30)
    if lobby_message_id:
        schedule_delete_id(bot, chat_id, lobby_message_id, delay=30)
