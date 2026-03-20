from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberAdministrator,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.mute_service import MuteService
from bot.domain.bot_utils import is_admin, parse_duration
from bot.domain.entities import MuteEntry
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import _ADMIN_PERM_FIELDS, _extract_admin_permissions
from bot.presentation.utils import reply_and_delete, schedule_delete, schedule_delete_id

logger = logging.getLogger(__name__)

router = Router(name="giveaway_mute_roulette")


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
        await reply_and_delete(message, "Только администраторы могут запускать мут-гивэвей.")
        return

    args = (message.text or "").split()[1:]
    # /mutegiveaway <время_мута> <кол-во_проигравших> <время_сбора>
    # /mutegiveaway 10m 2 5m
    if len(args) < 3:
        await reply_and_delete(
            message,
            "Использование: <code>/mutegiveaway &lt;мут&gt; &lt;кол-во&gt; &lt;сбор&gt;</code>\n"
            "Пример: <code>/mutegiveaway 10m 2 5m</code>\n"
            "= мут 10 минут, 2 проигравших, сбор участников 5 минут.",
            parse_mode="HTML",
        )
        return

    mute_secs = parse_duration(args[0])
    if mute_secs is None or mute_secs < 60:
        await reply_and_delete(message, "Неверное время мута (мин. 1m).")
        return

    try:
        losers_count = int(args[1])
        if losers_count < 1:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, "Кол-во проигравших должно быть >= 1.")
        return

    collect_secs = parse_duration(args[2])
    if collect_secs is None or collect_secs < 30:
        await reply_and_delete(message, "Время сбора минимум 30 секунд.")
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
    sent = await message.answer(text, parse_mode="HTML", reply_markup=_mute_roulette_kb(chat_id, roulette_id, 0))
    await store.mute_roulette_set_message_id(chat_id, roulette_id, sent.message_id)


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
        await reply_and_delete(message, "Только администраторы.")
        return

    chat_id = message.chat.id
    args = (message.text or "").split()[1:]

    roulette_id: str | None = args[0] if args else None

    if roulette_id is None:
        active = await store.mute_roulette_list(chat_id)
        if not active:
            await reply_and_delete(message, "Нет активных мут-гивэвеев.")
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
            await reply_and_delete(message, "\n".join(lines), parse_mode="HTML")
            return

    data = await store.mute_roulette_delete(chat_id, roulette_id)
    if data is None:
        await reply_and_delete(message, "❌ Мут-гивэвей не найден или уже завершён.")
        return

    await _finish_mute_roulette(bot, chat_id, data, mute_service)


async def _finish_mute_roulette(
    bot: Bot,
    chat_id: int,
    data: dict,
    mute_service: MuteService,
) -> None:
    """Завершение мут-гивэвея: выбор проигравших и применение мутов."""
    participants = data["participants"]
    losers_count = data["losers_count"]
    mute_minutes = data["mute_minutes"]
    creator_id = data["creator_id"]

    lobby_message_id: int = data.get("message_id", 0)

    if not participants:
        result_msg = await bot.send_message(chat_id, "🎰 Мут-гивэвей завершён, но никто не участвовал.")
        schedule_delete(bot, result_msg, delay=30)
        if lobby_message_id:
            schedule_delete_id(bot, chat_id, lobby_message_id, delay=30)
        return

    losers = random.sample(participants, min(losers_count, len(participants)))
    until = datetime.now(TZ_MSK) + timedelta(minutes=mute_minutes)

    lines = [f"🎰 <b>Мут-гивэвей завершён!</b> Участников: {len(participants)}\n"]
    for user_id in losers:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            name = f'<a href="tg://user?id={user_id}">{member.user.full_name}</a>'
        except Exception:
            name = f"<code>{user_id}</code>"
            member = None

        # Проверяем, является ли участник администратором
        was_admin = isinstance(member, ChatMemberAdministrator)
        admin_perms: dict | None = None
        if was_admin:
            admin_perms = _extract_admin_permissions(member)

        try:
            # Если админ — сначала снимаем права, потом мутим
            if was_admin:
                demote_kw = {f: False for f in _ADMIN_PERM_FIELDS}
                try:
                    await bot.promote_chat_member(chat_id=chat_id, user_id=user_id, **demote_kw)
                except TelegramBadRequest:
                    # Не удалось снять права — значит owner или выше наших прав
                    lines.append(f"🛡️ {name} — администратор, мут невозможен")
                    continue

            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await mute_service.save_mute(
                MuteEntry(
                    user_id=user_id,
                    chat_id=chat_id,
                    muted_by=creator_id,
                    until_at=until,
                    was_admin=was_admin,
                    admin_permissions=admin_perms,
                )
            )
            lines.append(f"🔇 {name} — мут {mute_minutes} мин")
        except TelegramBadRequest as e:
            err = str(e).lower()
            if any(w in err for w in ("not enough rights", "creator", "owner", "can't restrict", "administrator")):
                lines.append(f"🛡️ {name} — администратор, мут невозможен")
            else:
                logger.exception("Failed to mute %d in roulette", user_id)
                lines.append(f"⚠️ {name} — не удалось замутить")
        except Exception:
            logger.exception("Failed to mute %d in roulette", user_id)
            lines.append(f"⚠️ {name} — не удалось замутить")

    result_msg = await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    schedule_delete(bot, result_msg, delay=30)
    if lobby_message_id:
        schedule_delete_id(bot, chat_id, lobby_message_id, delay=30)
