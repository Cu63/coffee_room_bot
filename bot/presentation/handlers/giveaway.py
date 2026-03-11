from __future__ import annotations

import re
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

from bot.application.giveaway_service import GiveawayService
from bot.domain.giveaway_entities import Giveaway
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig

router = Router(name="giveaway")

# ─── Утилиты ────────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^(\d+)(m|h)$")


def _parse_duration(token: str) -> timedelta | None:
    m = _DURATION_RE.match(token.lower())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    return timedelta(minutes=value) if unit == "m" else timedelta(hours=value)


def _is_admin(username: str | None, config: AppConfig) -> bool:
    if not username:
        return False
    return username.lstrip("@").lower() in config.admin.users


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
    if not _is_admin(message.from_user and message.from_user.username, config):
        await message.answer("⛔ Только администраторы могут создавать розыгрыши.")
        return

    args = (message.text or "").split()[1:]
    if not args:
        await message.answer(
            "Использование: <code>/giveaway 500 100 50 [30m|2h]</code>\n"
            "Призовые места через пробел, в конце опционально — время.",
            parse_mode="HTML",
        )
        return

    ends_at: datetime | None = None
    duration = _parse_duration(args[-1])
    if duration is not None:
        args = args[:-1]
        ends_at = datetime.now().astimezone() + duration

    prizes: list[int] = []
    for token in args:
        if not token.isdigit() or int(token) <= 0:
            await message.answer(f"❌ Неверное значение приза: <code>{token}</code>", parse_mode="HTML")
            return
        prizes.append(int(token))

    if not prizes:
        await message.answer("❌ Укажи хотя бы один приз.")
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
    if not _is_admin(message.from_user and message.from_user.username, config):
        await message.answer("⛔ Только администраторы могут завершать розыгрыши.")
        return

    args = (message.text or "").split()[1:]

    giveaway_id: int | None = None
    if args and args[0].isdigit():
        giveaway_id = int(args[0])
    else:
        active = await service.get_active_in_chat(message.chat.id)
        if not active:
            await message.answer("Нет активных розыгрышей.")
            return
        if len(active) == 1:
            giveaway_id = active[0].id
        else:
            lines = ["Несколько активных розыгрышей, укажи ID:\n"]
            for g in active:
                prizes_str = " / ".join(f"{p} {pluralizer.pluralize(p)}" for p in g.prizes)
                lines.append(f"<code>/giveaway_end {g.id}</code> — {prizes_str}")
            await message.answer("\n".join(lines), parse_mode="HTML")
            return

    result = await service.finish(giveaway_id)
    if result is None:
        await message.answer("❌ Розыгрыш не найден или уже завершён.")
        return

    await _post_results(bot, result.giveaway, result.winners, result.participants_count, pluralizer)


# ─── Callback: кнопка «Участвовать» ─────────────────────────────────────────

@router.callback_query(F.data.startswith("giveaway:join:"))
@inject
async def cb_join(
    cb: CallbackQuery,
    service: FromDishka[GiveawayService],
) -> None:
    giveaway_id = int(cb.data.split(":")[2])
    user_id = cb.from_user.id

    joined = await service.join(giveaway_id, user_id)
    if not joined:
        await cb.answer("Ты уже участвуешь или розыгрыш завершён.", show_alert=False)
        return

    count = await service.count_participants(giveaway_id)
    await cb.answer("✅ Ты в игре!", show_alert=False)

    try:
        await cb.message.edit_reply_markup(reply_markup=_join_kb(giveaway_id, count))
    except Exception:
        pass


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

    await bot.send_message(giveaway.chat_id, text, parse_mode="HTML")

    if giveaway.message_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=giveaway.chat_id,
                message_id=giveaway.message_id,
                reply_markup=None,
            )
        except Exception:
            pass