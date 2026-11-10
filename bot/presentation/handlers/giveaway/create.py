from __future__ import annotations

import html
from datetime import datetime

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.giveaway_service import GiveawayService
from bot.domain.bot_utils import is_admin
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.giveaway.helpers import (
    _format_end_time,
    _format_prizes,
    _join_kb,
    _parse_duration_td,
    _parse_period,
    _period_label,
    _post_results,
)
from bot.presentation.utils import reply_and_delete

router = Router(name="giveaway_create")


# ─── /giveaway ──────────────────────────────────────────────────────────────


@router.message(Command("giveaway"))
@inject
async def cmd_giveaway(
    message: Message,
    bot: Bot,
    service: FromDishka[GiveawayService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    t = formatter._t
    if message.from_user is None or not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["giveaway_admins_only"])
        return

    args = (message.text or "").split()[1:]
    if not args:
        await reply_and_delete(message, t["giveaway_usage"], parse_mode="HTML")
        return

    ends_at: datetime | None = None
    # Время — только с единицей (30m, 2h): голое число в конце — это приз, как в подсказке «/giveaway 500 100 50»
    duration = None if args[-1].isdigit() else _parse_duration_td(args[-1])
    if duration is not None:
        args = args[:-1]
        ends_at = datetime.now(TZ_MSK) + duration

    prizes: list[int] = []
    for token in args:
        if not token.isdigit() or int(token) <= 0:
            await reply_and_delete(message, t["giveaway_bad_prize"].format(token=html.escape(token)), parse_mode="HTML")
            return
        prizes.append(int(token))

    if not prizes:
        await reply_and_delete(message, t["giveaway_no_prizes"])
        return

    giveaway = await service.create(
        chat_id=message.chat.id,
        created_by=message.from_user.id,
        prizes=prizes,
        ends_at=ends_at,
    )

    text = t["giveaway_started"].format(
        prizes=_format_prizes(prizes, pluralizer), ends=_format_end_time(ends_at, t), id=giveaway.id
    )
    sent = await message.answer(text, parse_mode="HTML", reply_markup=_join_kb(giveaway.id, 0, t))
    await service.set_message_id(giveaway.id, sent.message_id)


# ─── /giveaway_period ───────────────────────────────────────────────────────


@router.message(Command("giveaway_period"))
@inject
async def cmd_giveaway_period(
    message: Message,
    service: FromDishka[GiveawayService],
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Запускает периодический розыгрыш.

    Синтаксис: /giveaway_period <период> <приз1> [приз2 ...] [длительность_раунда]

    Примеры:
      /giveaway_period hourly 500 100       — ежечасно, без ограничения времени
      /giveaway_period daily 1000 500 2h    — ежедневно, каждый раунд 2 часа
      /giveaway_period weekly 5000 1000 1d  — еженедельно, раунд 1 день
      /giveaway_period 6h 200 100           — каждые 6 часов
    """
    t = formatter._t
    if message.from_user is None or not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["giveaway_period_admins_only"])
        return

    args = (message.text or "").split()[1:]
    if len(args) < 2:
        await reply_and_delete(message, t["giveaway_period_usage"], parse_mode="HTML")
        return

    period_seconds = _parse_period(args[0])
    if period_seconds is None:
        await reply_and_delete(
            message, t["giveaway_period_bad_period"].format(period=html.escape(args[0])), parse_mode="HTML"
        )
        return

    rest = args[1:]
    # Последний аргумент может быть длительностью раунда
    round_duration = None
    if rest:
        dur = None if rest[-1].isdigit() else _parse_duration_td(rest[-1])  # голое число — приз
        if dur is not None:
            round_duration = dur
            rest = rest[:-1]

    prizes: list[int] = []
    for token in rest:
        if not token.isdigit() or int(token) <= 0:
            await reply_and_delete(message, t["giveaway_bad_prize"].format(token=html.escape(token)), parse_mode="HTML")
            return
        prizes.append(int(token))

    if not prizes:
        await reply_and_delete(message, t["giveaway_no_prizes"])
        return

    round_dur_secs = int(round_duration.total_seconds()) if round_duration else None
    gp_id = await store.giveaway_period_create(
        chat_id=message.chat.id,
        created_by=message.from_user.id,
        prizes=prizes,
        period_seconds=period_seconds,
        round_duration_seconds=round_dur_secs,
    )

    round_str = t["giveaway_manual"]
    if round_duration:
        total_minutes = int(round_duration.total_seconds() // 60)
        if total_minutes >= 60:
            round_str = t["giveaway_unit_hours"].format(n=total_minutes // 60)
        else:
            round_str = t["giveaway_unit_minutes"].format(n=total_minutes)

    await reply_and_delete(
        message,
        t["giveaway_period_created"].format(
            prizes=_format_prizes(prizes, pluralizer),
            period=_period_label(period_seconds, t),
            round=round_str,
            id=gp_id,
        ),
        parse_mode="HTML",
        delay=60,
    )


# ─── /giveaway_period_stop ───────────────────────────────────────────────────


@router.message(Command("giveaway_period_stop"))
@inject
async def cmd_giveaway_period_stop(
    message: Message,
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Останавливает периодический розыгрыш по ID или единственный активный."""
    t = formatter._t
    if message.from_user is None or not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["giveaway_period_stop_admins_only"])
        return

    chat_id = message.chat.id
    args = (message.text or "").split()[1:]

    gp_id: str | None = args[0] if args else None

    if gp_id is None:
        active = await store.giveaway_period_list(chat_id)
        if not active:
            await reply_and_delete(message, t["giveaway_period_none"])
            return
        if len(active) == 1:
            gp_id = active[0][0]
        else:
            lines = [t["giveaway_period_choose"]]
            for pid, data in active:
                prizes_str = " / ".join(f"{p} {pluralizer.pluralize(p)}" for p in data["prizes"])
                lines.append(
                    t["giveaway_period_choose_row"].format(
                        id=pid, prizes=prizes_str, period=_period_label(data["period_seconds"], t)
                    )
                )
            await reply_and_delete(message, "\n".join(lines), parse_mode="HTML")
            return

    data = await store.giveaway_period_delete(chat_id, gp_id)
    if data is None:
        await reply_and_delete(message, t["giveaway_period_not_found"])
        return

    prizes_str = " / ".join(f"{p} {pluralizer.pluralize(p)}" for p in data["prizes"])
    await reply_and_delete(
        message, t["giveaway_period_stopped"].format(id=html.escape(gp_id), prizes=prizes_str), parse_mode="HTML"
    )


# ─── /giveaway_end ──────────────────────────────────────────────────────────


@router.message(Command("giveaway_end"))
@inject
async def cmd_giveaway_end(
    message: Message,
    bot: Bot,
    service: FromDishka[GiveawayService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    formatter: FromDishka[MessageFormatter],
) -> None:
    t = formatter._t
    if message.from_user is None or not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["giveaway_end_admins_only"])
        return

    args = (message.text or "").split()[1:]

    giveaway_id: int | None = None
    if args and args[0].isdigit():
        giveaway_id = int(args[0])
    else:
        active = await service.get_active_in_chat(message.chat.id)
        if not active:
            await reply_and_delete(message, t["giveaway_none"])
            return
        if len(active) == 1:
            giveaway_id = active[0].id
        else:
            lines = [t["giveaway_choose"]]
            for g in active:
                prizes_str = " / ".join(f"{p} {pluralizer.pluralize(p)}" for p in g.prizes)
                lines.append(t["giveaway_choose_row"].format(id=g.id, prizes=prizes_str))
            await reply_and_delete(message, "\n".join(lines), parse_mode="HTML")
            return

    result = await service.finish(giveaway_id)
    if result is None:
        await reply_and_delete(message, t["giveaway_not_found"])
        return

    await _post_results(bot, result.giveaway, result.winners, result.participants_count, pluralizer, t)
