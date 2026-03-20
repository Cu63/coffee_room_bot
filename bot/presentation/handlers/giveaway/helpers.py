from __future__ import annotations

import re
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain.bot_utils import parse_duration
from bot.domain.giveaway_entities import Giveaway
from bot.domain.pluralizer import ScorePluralizer
from bot.presentation.utils import schedule_delete, schedule_delete_id

# ─── Утилиты ────────────────────────────────────────────────────────────────

# Алиасы периодов и расширенный формат (m/h/d/w)
_PERIOD_ALIASES: dict[str, int] = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}
_PERIOD_RE = re.compile(r"^(\d+)(m|h|d|w)$")


def _parse_duration_td(token: str) -> timedelta | None:
    """Обёртка над parse_duration, возвращающая timedelta."""
    secs = parse_duration(token)
    return timedelta(seconds=secs) if secs is not None else None


def _parse_period(token: str) -> int | None:
    """Разобрать период гивэвея в секундах.

    Поддерживаемые форматы: hourly, daily, weekly, Xm, Xh, Xd, Xw.
    """
    ltoken = token.lower()
    if ltoken in _PERIOD_ALIASES:
        return _PERIOD_ALIASES[ltoken]
    m = _PERIOD_RE.match(ltoken)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    secs = value * multipliers[unit]
    return secs if secs >= 60 else None


def _period_label(period_seconds: int) -> str:
    """Человекочитаемое название периода."""
    if period_seconds % 604800 == 0:
        n = period_seconds // 604800
        return f"{n}н" if n > 1 else "еженедельно"
    if period_seconds % 86400 == 0:
        n = period_seconds // 86400
        return f"{n}д" if n > 1 else "ежедневно"
    if period_seconds % 3600 == 0:
        n = period_seconds // 3600
        return f"{n}ч" if n > 1 else "ежечасно"
    if period_seconds % 60 == 0:
        return f"{period_seconds // 60}м"
    return f"{period_seconds}с"


# is_admin imported from bot.domain.bot_utils


def _join_kb(giveaway_id: int, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎟 Участвовать ({count})",
                    callback_data=f"giveaway:join:{giveaway_id}",
                )
            ]
        ]
    )


def _format_prizes(prizes: list[int], pluralizer: ScorePluralizer) -> str:
    medals = ["🥇", "🥈", "🥉"]
    parts = []
    for i, prize in enumerate(prizes):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        parts.append(f"{medal} {prize} {pluralizer.pluralize(prize)}")
    return "\n".join(parts)


def _format_end_time(ends_at: datetime | None) -> str:
    if ends_at is None:
        return "вручную"
    return ends_at.strftime("%d.%m %H:%M")


# ─── Общая функция публикации результатов ───────────────────────────────────


async def _post_results(
    bot: Bot,
    giveaway: Giveaway,
    winners: list[tuple[int, int]],
    participants_count: int,
    pluralizer: ScorePluralizer,
) -> None:
    medals = ["🥇", "🥈", "🥉"]

    if not winners:
        text = "🎰 Розыгрыш завершён, но никто не участвовал 😔"
    else:
        lines = [f"🎊 <b>Розыгрыш завершён!</b> Участников: {participants_count}\n"]
        for i, (user_id, prize) in enumerate(winners):
            medal = medals[i] if i < len(medals) else f"{i + 1}."
            prize_str = f"{prize} {pluralizer.pluralize(prize)}"
            try:
                chat_member = await bot.get_chat_member(giveaway.chat_id, user_id)
                mention = f'<a href="tg://user?id={user_id}">{chat_member.user.full_name}</a>'
            except Exception:
                mention = f"<code>{user_id}</code>"
            lines.append(f"{medal} {mention} — +{prize_str}")
        text = "\n".join(lines)

    result_msg = await bot.send_message(giveaway.chat_id, text, parse_mode="HTML")
    schedule_delete(bot, result_msg, delay=30)

    if giveaway.message_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=giveaway.chat_id,
                message_id=giveaway.message_id,
                reply_markup=None,
            )
        except Exception:
            pass
        schedule_delete_id(bot, giveaway.chat_id, giveaway.message_id, delay=30)
