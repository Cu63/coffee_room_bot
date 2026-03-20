from __future__ import annotations

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
) -> None:
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
        await reply_and_delete(message, "⛔ Только администраторы могут создавать розыгрыши.")
        return

    args = (message.text or "").split()[1:]
    if not args:
        await reply_and_delete(
            message,
            "Использование: <code>/giveaway 500 100 50 [30m|2h]</code>\n"
            "Призовые места через пробел, в конце опционально — время.",
            parse_mode="HTML",
        )
        return

    ends_at: datetime | None = None
    duration = _parse_duration_td(args[-1])
    if duration is not None:
        args = args[:-1]
        ends_at = datetime.now(TZ_MSK) + duration

    prizes: list[int] = []
    for token in args:
        if not token.isdigit() or int(token) <= 0:
            await reply_and_delete(message, f"❌ Неверное значение приза: <code>{token}</code>", parse_mode="HTML")
            return
        prizes.append(int(token))

    if not prizes:
        await reply_and_delete(message, "❌ Укажи хотя бы один приз.")
        return

    giveaway = await service.create(
        chat_id=message.chat.id,
        created_by=message.from_user.id,
        prizes=prizes,
        ends_at=ends_at,
    )

    text = (
        "🎉 <b>Розыгрыш запущен!</b>\n\n"
        f"{_format_prizes(prizes, pluralizer)}\n\n"
        f"⏰ Завершение: <b>{_format_end_time(ends_at)}</b>\n"
        f"🆔 ID: <code>{giveaway.id}</code>"
    )
    sent = await message.answer(text, parse_mode="HTML", reply_markup=_join_kb(giveaway.id, 0))
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
) -> None:
    """Запускает периодический розыгрыш.

    Синтаксис: /giveaway_period <период> <приз1> [приз2 ...] [длительность_раунда]

    Примеры:
      /giveaway_period hourly 500 100       — ежечасно, без ограничения времени
      /giveaway_period daily 1000 500 2h    — ежедневно, каждый раунд 2 часа
      /giveaway_period weekly 5000 1000 1d  — еженедельно, раунд 1 день
      /giveaway_period 6h 200 100           — каждые 6 часов
    """
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
        await reply_and_delete(message, "⛔ Только администраторы могут создавать периодические розыгрыши.")
        return

    args = (message.text or "").split()[1:]
    if len(args) < 2:
        await reply_and_delete(
            message,
            "Использование: <code>/giveaway_period &lt;период&gt; &lt;приз1&gt; [приз2 ...] [длительность]</code>\n\n"
            "Периоды: <code>hourly</code>, <code>daily</code>, <code>weekly</code>, <code>6h</code>, <code>30m</code> и т.д.\n"
            "Длительность раунда (опционально): <code>30m</code>, <code>2h</code>\n\n"
            "Пример: <code>/giveaway_period daily 500 100 2h</code>",
            parse_mode="HTML",
        )
        return

    period_seconds = _parse_period(args[0])
    if period_seconds is None:
        await reply_and_delete(
            message,
            f"❌ Неверный период: <code>{args[0]}</code>\n"
            "Используй: <code>hourly</code>, <code>daily</code>, <code>weekly</code> или формат <code>Xm/Xh/Xd/Xw</code>",
            parse_mode="HTML",
        )
        return

    rest = args[1:]
    # Последний аргумент может быть длительностью раунда
    round_duration = None
    if rest:
        dur = _parse_duration_td(rest[-1])
        if dur is not None:
            round_duration = dur
            rest = rest[:-1]

    prizes: list[int] = []
    for token in rest:
        if not token.isdigit() or int(token) <= 0:
            await reply_and_delete(message, f"❌ Неверное значение приза: <code>{token}</code>", parse_mode="HTML")
            return
        prizes.append(int(token))

    if not prizes:
        await reply_and_delete(message, "❌ Укажи хотя бы один приз.")
        return

    round_dur_secs = int(round_duration.total_seconds()) if round_duration else None
    gp_id = await store.giveaway_period_create(
        chat_id=message.chat.id,
        created_by=message.from_user.id,
        prizes=prizes,
        period_seconds=period_seconds,
        round_duration_seconds=round_dur_secs,
    )

    period_str = _period_label(period_seconds)
    prizes_str = _format_prizes(prizes, pluralizer)
    round_str = f"{round_duration}" if round_duration else "вручную"
    if round_duration:
        total_minutes = int(round_duration.total_seconds() // 60)
        if total_minutes >= 60:
            round_str = f"{total_minutes // 60}ч"
        else:
            round_str = f"{total_minutes}м"

    await reply_and_delete(
        message,
        f"🔁 <b>Периодический розыгрыш создан!</b>\n\n"
        f"{prizes_str}\n\n"
        f"🕐 Период: <b>{period_str}</b>\n"
        f"⏰ Длительность раунда: <b>{round_str}</b>\n"
        f"🆔 GP ID: <code>{gp_id}</code>\n\n"
        f"Первый раунд стартует через ~1 минуту.\n"
        f"Остановить: <code>/giveaway_period_stop {gp_id}</code>",
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
) -> None:
    """Останавливает периодический розыгрыш по ID или единственный активный."""
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
        await reply_and_delete(message, "⛔ Только администраторы могут останавливать периодические розыгрыши.")
        return

    chat_id = message.chat.id
    args = (message.text or "").split()[1:]

    gp_id: str | None = args[0] if args else None

    if gp_id is None:
        active = await store.giveaway_period_list(chat_id)
        if not active:
            await reply_and_delete(message, "Нет активных периодических розыгрышей.")
            return
        if len(active) == 1:
            gp_id = active[0][0]
        else:
            lines = ["Несколько активных периодических розыгрышей, укажи ID:\n"]
            for pid, data in active:
                prizes_str = " / ".join(f"{p} {pluralizer.pluralize(p)}" for p in data["prizes"])
                lines.append(
                    f"<code>/giveaway_period_stop {pid}</code> — {prizes_str}, {_period_label(data['period_seconds'])}"
                )
            await reply_and_delete(message, "\n".join(lines), parse_mode="HTML")
            return

    data = await store.giveaway_period_delete(chat_id, gp_id)
    if data is None:
        await reply_and_delete(message, "❌ Периодический розыгрыш не найден.")
        return

    prizes_str = " / ".join(f"{p} {pluralizer.pluralize(p)}" for p in data["prizes"])
    await reply_and_delete(
        message,
        f"⏹ Периодический розыгрыш <code>{gp_id}</code> остановлен.\n"
        f"Призы были: {prizes_str}",
        parse_mode="HTML",
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
) -> None:
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
        await reply_and_delete(message, "⛔ Только администраторы могут завершать розыгрыши.")
        return

    args = (message.text or "").split()[1:]

    giveaway_id: int | None = None
    if args and args[0].isdigit():
        giveaway_id = int(args[0])
    else:
        active = await service.get_active_in_chat(message.chat.id)
        if not active:
            await reply_and_delete(message, "Нет активных розыгрышей.")
            return
        if len(active) == 1:
            giveaway_id = active[0].id
        else:
            lines = ["Несколько активных розыгрышей, укажи ID:\n"]
            for g in active:
                prizes_str = " / ".join(f"{p} {pluralizer.pluralize(p)}" for p in g.prizes)
                lines.append(f"<code>/giveaway_end {g.id}</code> — {prizes_str}")
            await reply_and_delete(message, "\n".join(lines), parse_mode="HTML")
            return

    result = await service.finish(giveaway_id)
    if result is None:
        await reply_and_delete(message, "❌ Розыгрыш не найден или уже завершён.")
        return

    await _post_results(bot, result.giveaway, result.winners, result.participants_count, pluralizer)
