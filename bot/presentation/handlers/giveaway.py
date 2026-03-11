from __future__ import annotations

import random
import re
from datetime import datetime, timedelta

from bot.domain.tz import TZ_MSK

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.giveaway_service import GiveawayService
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.domain.entities import MuteEntry, User
from bot.domain.giveaway_entities import Giveaway
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.bot_utils import is_admin, parse_duration
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore

import logging

logger = logging.getLogger(__name__)

router = Router(name="giveaway")

# ─── Утилиты ────────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^(\d+)(m|h)$")


def _parse_duration(token: str) -> timedelta | None:
    m = _DURATION_RE.match(token.lower())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    return timedelta(minutes=value) if unit == "m" else timedelta(hours=value)


# is_admin imported from bot.domain.bot_utils


def _join_kb(chat_id: int, roulette_id: str, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎟 Участвовать ({count})",
                    callback_data=f"giveaway:join:{chat_id}",
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
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
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
        ends_at = datetime.now(TZ_MSK) + duration

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
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
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
    user_repo: FromDishka[IUserRepository],
) -> None:
    giveaway_id = int(cb.data.split(":")[2])
    user_id = cb.from_user.id

    # Upsert пользователя — иначе FK на scores/users упадёт при начислении баллов
    await user_repo.upsert(User(
        id=user_id,
        username=cb.from_user.username,
        full_name=cb.from_user.full_name,
    ))

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


# ─── Мут-рулетка ────────────────────────────────────────────────────────────


def _mute_roulette_kb(chat_id: int, roulette_id: str, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎰 Испытать удачу ({count})",
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
) -> None:
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
        await message.answer("Только администраторы могут запускать мут-гивэвей.")
        return

    args = (message.text or "").split()[1:]
    # /mutegiveaway <время_мута> <кол-во_проигравших> <время_сбора>
    # /mutegiveaway 10m 2 5m
    if len(args) < 3:
        await message.answer(
            "Использование: <code>/mutegiveaway &lt;мут&gt; &lt;кол-во&gt; &lt;сбор&gt;</code>\n"
            "Пример: <code>/mutegiveaway 10m 2 5m</code>\n"
            "= мут 10 минут, 2 проигравших, сбор участников 5 минут.",
            parse_mode="HTML",
        )
        return

    mute_secs = parse_duration(args[0])
    if mute_secs is None or mute_secs < 60:
        await message.answer("Неверное время мута (мин. 1m).")
        return

    try:
        losers_count = int(args[1])
        if losers_count < 1:
            raise ValueError
    except ValueError:
        await message.answer("Кол-во проигравших должно быть >= 1.")
        return

    collect_secs = parse_duration(args[2])
    if collect_secs is None or collect_secs < 30:
        await message.answer("Время сбора минимум 30 секунд.")
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

    collect_str = f"{collect_secs // 60}м" if collect_secs >= 60 else f"{collect_secs}с"
    text = (
        f"🎰 <b>Мут-гивэвей!</b>\n\n"
        f"Время мута: <b>{mute_minutes} мин</b>\n"
        f"Проигравших: <b>{losers_count}</b>\n"
        f"Сбор: <b>{collect_str}</b>\n"
        f"🆔 ID: <code>{roulette_id}</code>\n\n"
        f"Жми кнопку, если не трус!"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_mute_roulette_kb(chat_id, roulette_id, 0))


@router.callback_query(F.data.startswith("mutegiveaway:join:"))
@inject
async def cb_mute_roulette_join(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
) -> None:
    parts = cb.data.split(":")
    chat_id = int(parts[2])
    roulette_id = parts[3]
    user_id = cb.from_user.id

    joined = await store.mute_roulette_join(chat_id, roulette_id, user_id)
    if not joined:
        await cb.answer("Ты уже участвуешь или рулетка завершена.", show_alert=False)
        return

    count = await store.mute_roulette_count(chat_id, roulette_id)
    await cb.answer("Ты в игре! Удачи...", show_alert=False)

    try:
        await cb.message.edit_reply_markup(reply_markup=_mute_roulette_kb(chat_id, roulette_id, count))
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
) -> None:
    if not is_admin(message.from_user and message.from_user.username, config.admin.users):
        await message.answer("Только администраторы.")
        return

    chat_id = message.chat.id
    args = (message.text or "").split()[1:]

    roulette_id: str | None = args[0] if args else None

    if roulette_id is None:
        active = await store.mute_roulette_list(chat_id)
        if not active:
            await message.answer("Нет активных мут-гивэвеев.")
            return
        if len(active) == 1:
            roulette_id = active[0][0]
        else:
            lines = ["Несколько активных мут-гивэвеев, укажи ID:\n"]
            for rid, data in active:
                lines.append(
                    f"<code>/mutegiveaway_end {rid}</code> — "
                    f"мут {data['mute_minutes']}м, "
                    f"проигравших {data['losers_count']}, "
                    f"участников {len(data['participants'])}"
                )
            await message.answer("\n".join(lines), parse_mode="HTML")
            return

    data = await store.mute_roulette_delete(chat_id, roulette_id)
    if data is None:
        await message.answer("❌ Мут-гивэвей не найден или уже завершён.")
        return

    await _finish_mute_roulette(bot, chat_id, data, mute_service)


async def _finish_mute_roulette(
    bot: Bot, chat_id: int, data: dict, mute_service: MuteService,
) -> None:
    """Завершение мут-рулетки: выбор проигравших и применение мутов."""
    participants = data["participants"]
    losers_count = data["losers_count"]
    mute_minutes = data["mute_minutes"]

    if not participants:
        await bot.send_message(chat_id, "🎰 Мут-рулетка завершена, но никто не участвовал.")
        return

    losers = random.sample(participants, min(losers_count, len(participants)))
    until = datetime.now(TZ_MSK) + timedelta(minutes=mute_minutes)

    lines = [f"🎰 <b>Мут-рулетка завершена!</b> Участников: {len(participants)}\n"]
    for user_id in losers:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            name = f'<a href="tg://user?id={user_id}">{member.user.full_name}</a>'
        except Exception:
            name = f"<code>{user_id}</code>"

        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await mute_service.save_mute(MuteEntry(
                user_id=user_id,
                chat_id=chat_id,
                muted_by=0,
                until_at=until,
            ))
            lines.append(f"🔇 {name} — мут {mute_minutes} мин")
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "not enough rights" in err or "creator" in err or "can't restrict" in err or "administrator" in err:
                lines.append(f"🛡️ {name} — администратор, мут невозможен")
            else:
                logger.exception("Failed to mute %d in roulette", user_id)
                lines.append(f"⚠️ {name} — не удалось замутить")
        except Exception:
            logger.exception("Failed to mute %d in roulette", user_id)
            lines.append(f"⚠️ {name} — не удалось замутить")

    await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")