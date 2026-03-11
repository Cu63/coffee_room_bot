"""Команды бота.

Включает:
- Админские: /add, /sub, /set, /reset
- Админские: /save, /restore, /op
- Пользовательские: /mute, /selfmute, /tag, /transfer, /protect
- Справка: /help (интерактивная, привязана к вызвавшему)

"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    ChatMemberAdministrator,
    ChatMemberOwner,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import ScoreService, SPECIAL_EMOJI
from bot.application.mute_service import MuteService
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.saved_permissions_repository import ISavedPermissionsRepository
from bot.application.interfaces.mute_protection_repository import IMuteProtectionRepository
from bot.domain.entities import MuteEntry
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

ADMIN_PERM_FIELDS = (
    "can_manage_chat",
    "can_change_info",
    "can_delete_messages",
    "can_invite_users",
    "can_restrict_members",
    "can_pin_messages",
    "can_manage_video_chats",
    "can_promote_members",
    "can_post_messages",
    "can_edit_messages",
    "can_post_stories",
    "can_edit_stories",
    "can_delete_stories",
    "can_manage_topics",
    "can_manage_direct_messages",
    "can_manage_tags",
)

MODERATOR_PERMS = {
    "can_delete_messages": True,
    "can_restrict_members": True,
    "can_invite_users": True,
    "can_pin_messages": True,
    "can_manage_video_chats": True,
    "can_manage_topics": True,
}


# ── Вспомогательные функции ───────────────────────────────────────

def _extract_admin_permissions(member: ChatMemberAdministrator) -> dict:
    perms: dict = {}
    for field in ADMIN_PERM_FIELDS:
        perms[field] = getattr(member, field, False) or False
    if member.custom_title:
        perms["custom_title"] = member.custom_title
    return perms


def _promote_kwargs(perms: dict) -> dict:
    return {k: v for k, v in perms.items() if k in ADMIN_PERM_FIELDS}


def _is_admin(username: str | None, admins: list[str]) -> bool:
    if not username:
        return False
    return username.lower() in admins


def _parse_args_user_number(args: str | None) -> tuple[str, int] | None:
    if not args:
        return None
    parts = args.strip().split()
    if len(parts) != 2:
        return None
    username = parts[0].lstrip("@")
    try:
        n = int(parts[1])
    except ValueError:
        return None
    return (username, n)


async def _resolve_user_and_number(args, user_repo):
    parsed = _parse_args_user_number(args)
    if parsed is None:
        return None
    username, n = parsed
    user = await user_repo.get_by_username(username)
    return (user, n)


async def _resolve_mute_args(args, message, user_repo):
    """Разбирает аргументы /mute с поддержкой reply.

    Варианты:
      /mute @username 30    — явный username + минуты (старое поведение)
      /mute 30              — только минуты, цель берётся из reply
    Возвращает (User | None, minutes) или None если аргументы невалидны.
    """
    from bot.domain.entities import User as DomainUser

    args_str = (args or "").strip()
    parts = args_str.split()

    # Вариант 1: @username <время>  — поддерживает и голое число, и форматы типа 10m/2h
    if len(parts) == 2:
        username = parts[0].lstrip("@")
        seconds = _parse_duration(parts[1])
        if seconds is not None:
            minutes = max(1, seconds // 60)
            user = await user_repo.get_by_username(username)
            return (user, minutes)
        return None

    # Вариант 2: только время — цель из reply
    if len(parts) == 1:
        seconds = _parse_duration(parts[0])
        if seconds is None:
            return None
        minutes = max(1, seconds // 60)
        reply = message.reply_to_message
        if reply is None or reply.from_user is None:
            return None
        tg_user = reply.from_user
        target = DomainUser(
            id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name or str(tg_user.id),
        )
        return (target, minutes)

    return None


def _admin_reply(formatter: MessageFormatter, target, new_value: int) -> str:
    display = user_link(target.username, target.full_name, target.id)
    return formatter._t["admin_score_set"].format(
        user=display,
        total=new_value,
        score_word=formatter._p.pluralize(abs(new_value)),
    )


async def _resolve_username(args: str | None, user_repo: IUserRepository):
    if not args:
        return None
    username = args.strip().lstrip("@")
    return await user_repo.get_by_username(username)


# ── Парсер времени ───────────────────────────────────────────────

def _parse_duration(arg: str) -> int | None:
    """Парсит длительность в секунды.

    Поддерживаемые форматы (можно комбинировать):
      1d1h10m  1d  2h  30m  45s  1h30m  1d12h
    Суффиксы: d/д (дни), h/ч (часы), m/м (минуты), s/с (секунды).
    Без суффикса — трактуется как минуты.
    Возвращает количество секунд или None при ошибке парсинга.
    """
    arg = arg.strip().lower()

    # Составной формат: 1d2h30m45s (любая комбинация компонентов)
    pattern = re.compile(
        r'^(?:(\d+)[dд])?(?:(\d+)[hч])?(?:(\d+)[mм])?(?:(\d+)[sс])?$'
    )
    m = pattern.fullmatch(arg)
    if m and any(m.groups()):
        days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups())
        return days * 86400 + hours * 3600 + minutes * 60 + seconds

    # Голое число — трактуем как минуты
    try:
        return int(arg) * 60
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    """Форматирует секунды в читаемую строку: '1д 2ч 5м', '30м', '45с'."""
    parts = []
    if seconds >= 86400:
        d = seconds // 86400
        parts.append(f"{d}д")
        seconds %= 86400
    if seconds >= 3600:
        h = seconds // 3600
        parts.append(f"{h}ч")
        seconds %= 3600
    if seconds >= 60:
        m = seconds // 60
        parts.append(f"{m}м")
        seconds %= 60
    if seconds > 0:
        parts.append(f"{seconds}с")
    return " ".join(parts) or "0с"



# ── Интерактивная справка ─────────────────────────────────────────
# callback_data формат: "help:{section}:{caller_user_id}"

def _cb(section: str, uid: int) -> str:
    return f"help:{section}:{uid}"


def _help_main_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Реакции",      callback_data=_cb("reactions", uid)),
            InlineKeyboardButton(text="⚙️ Лимиты",       callback_data=_cb("limits", uid)),
        ],
        [
            InlineKeyboardButton(text="🔇 Мут",          callback_data=_cb("mute", uid)),
            InlineKeyboardButton(text="🏷 Тег",          callback_data=_cb("tag", uid)),
        ],
        [
            InlineKeyboardButton(text="🃏 Блекджек",     callback_data=_cb("bj", uid)),
            InlineKeyboardButton(text="📋 Команды",      callback_data=_cb("commands", uid)),
        ],
        [
            InlineKeyboardButton(text="🛡 Для админов",  callback_data=_cb("admin", uid)),
        ],
    ])


def _help_back_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=_cb("main", uid))]
    ])


def _help_main_text(icon: str) -> str:
    return (
        f"{icon} <b>Справка по боту</b>\n\n"
        "Выбери раздел, чтобы узнать подробнее:"
    )


def _help_section_text(section: str, config: AppConfig, formatter: MessageFormatter) -> str:
    p = formatter._p
    mc = config.mute
    tc = config.tag
    bjc = config.blackjack
    lc = config.limits

    if section == "reactions":
        lines = []
        for emoji, weight in config.reactions.items():
            sign = f"+{weight}" if weight > 0 else str(weight)
            lines.append(f"  {emoji} → {sign} {p.pluralize(abs(weight))}")
        return (
            "📊 <b>Реакции</b>\n\n"
            "Ставь реакции на сообщения — автор получает или теряет баллы.\n\n"
            + "\n".join(lines) + "\n\n"
            "Реакции на свои сообщения не засчитываются.\n"
            "Негативные реакции от участников с отрицательным счётом игнорируются."
        )

    if section == "limits":
        return (
            "⚙️ <b>Лимиты</b>\n\n"
            f"  Реакций в сутки (от одного): {lc.daily_reactions_given}\n"
            f"  Макс. баллов получателю в сутки: {lc.daily_score_received}\n"
            f"  Возраст сообщения: не старше {lc.max_message_age_hours} ч.\n"
            f"  История хранится: {config.history.retention_days} дн."
        )

    if section == "mute":
        return (
            "🔇 <b>Мут</b>\n\n"
            f"  Стоимость: {mc.cost_per_minute} {p.pluralize(mc.cost_per_minute)} / мин\n"
            f"  Длительность: {mc.min_minutes}–{mc.max_minutes} мин\n"
            "  Баллы в долг не даются\n\n"
            "<b>Самомут (бесплатно):</b>\n"
            f"  /selfmute — замутить себя ({mc.selfmute_min_minutes}–{mc.selfmute_max_minutes} мин)\n\n"
            "<b>Защита от мута:</b>\n"
            f"  /protect — купить защиту на {mc.protection_duration_hours} ч. за {mc.protection_cost} б.\n"
            "  Защита блокирует /mute от любого участника\n"
            "  Повторный /protect продлевает защиту\n"
            "  <b>/amute от админа обходит защиту</b>\n\n"
            "Форматы времени: 30, 30m, 2h, 1d, 1d2h30m\n\n"
            "Если цель — администратор, назначенный ботом (/op или /restore),\n"
            "мут временно снимает ей права."
        )

    if section == "tag":
        return (
            "🏷 <b>Смена тега</b>\n\n"
            f"  Себе: {tc.cost_self} {p.pluralize(tc.cost_self)}\n"
            f"  Участнику: {tc.cost_member} {p.pluralize(tc.cost_member)}\n"
            f"  Админу: {tc.cost_admin} {p.pluralize(tc.cost_admin)}\n"
            f"  Создателю: {tc.cost_owner} {p.pluralize(tc.cost_owner)}\n"
            f"  Макс. длина: {tc.max_length} символов\n\n"
            "  --clear — удалить тег"
        )

    if section == "bj":
        return (
            "🃏 <b>Блекджек</b>\n\n"
            f"  Ставка: {bjc.min_bet}–{bjc.max_bet} {p.pluralize(bjc.max_bet)}\n"
            "  Блекджек (21 с двух карт): ×1.5\n"
            "  Выигрыш: ×1, ничья: возврат ставки\n\n"
            "  /bj &lt;ставка&gt; — начать игру\n"
            "  /help_bj — подробные правила"
        )

    if section == "commands":
        return (
            "📋 <b>Команды</b>\n\n"
            "  /score — твой счёт\n"
            "  /score @user — счёт пользователя\n"
            "  /top [N] — таблица лидеров\n"
            "  /history — история начислений\n"
            "  /limits — текущие лимиты бота\n"
            f"  /bj &lt;ставка&gt; — блекджек ({bjc.min_bet}–{bjc.max_bet})\n"
            f"  /mute @user N — платный мут для всех ({mc.min_minutes}–{mc.max_minutes} мин)\n"
            f"  /selfmute N — самомут бесплатно ({mc.selfmute_min_minutes}–{mc.selfmute_max_minutes} мин)\n"
            f"  /protect — защита от /mute на {mc.protection_duration_hours} ч. за {mc.protection_cost} б.\n"
            "  /transfer @user N — перевести N баллов пользователю\n"
            "  /tag [тег] — сменить свой тег\n"
            "  /tag @user [тег] — сменить чужой тег\n"
            "  /help — эта справка"
        )

    if section == "admin":
        return (
            "🛡 <b>Команды для админов</b>\n\n"
            "  /add @user N — добавить N баллов\n"
            "  /sub @user N — вычесть N баллов\n"
            "  /set @user N — установить баллы\n"
            "  /reset @user — обнулить баллы\n\n"
            "  /op @user — назначить модератором (через бота)\n"
            "  /amute @user N — мут без оплаты и обходит /protect\n"
            "  /unmute @user — снять мут досрочно\n"
            "  /save @user — сохранить права админа\n"
            "  /restore @user — восстановить права ботом"
        )

    return ""


# ── Снятие мута (используется в main.py и внутри) ─────────────────

async def _unmute_user(bot, mute_service: MuteService, entry: MuteEntry) -> None:
    try:
        await bot.restrict_chat_member(
            chat_id=entry.chat_id,
            user_id=entry.user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
                can_change_info=True,
                can_pin_messages=True,
                can_manage_topics=True,
            ),
        )
    except Exception:
        logger.exception("Failed to unrestrict user %d in chat %d", entry.user_id, entry.chat_id)

    if entry.was_admin and entry.admin_permissions:
        try:
            kw = _promote_kwargs(entry.admin_permissions)
            await bot.promote_chat_member(
                chat_id=entry.chat_id,
                user_id=entry.user_id,
                **kw,
            )
            if entry.admin_permissions.get("custom_title"):
                await bot.set_chat_administrator_custom_title(
                    chat_id=entry.chat_id,
                    user_id=entry.user_id,
                    custom_title=entry.admin_permissions["custom_title"],
                )
        except Exception:
            logger.exception(
                "Failed to restore admin rights for user %d in chat %d",
                entry.user_id, entry.chat_id,
            )

    await mute_service.delete_mute(entry.user_id, entry.chat_id)


# ── Роутер ────────────────────────────────────────────────────────

def create_admin_router(prefix: str) -> Router:  # prefix оставлен для совместимости
    router = Router(name="admin_commands")

    # ── /reset ────────────────────────────────────────────────────

    @router.message(Command("reset"))
    @inject
    async def cmd_reset(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        if not command.args:
            await message.reply(formatter._t["admin_usage_reset"])
            return
        username = command.args.strip().lstrip("@")
        target = await user_repo.get_by_username(username)
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        new_value = await score_service.set_score(
            target.id, message.chat.id, 0, admin_id=message.from_user.id,
        )
        await message.reply(
            _admin_reply(formatter, target, new_value),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /set ──────────────────────────────────────────────────────

    @router.message(Command("set"))
    @inject
    async def cmd_set(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        parsed = await _resolve_user_and_number(command.args, user_repo)
        if parsed is None:
            await message.reply(formatter._t["admin_usage_set"])
            return
        target, amount = parsed
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        new_value = await score_service.set_score(
            target.id, message.chat.id, amount, admin_id=message.from_user.id,
        )
        await message.reply(
            _admin_reply(formatter, target, new_value),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /add ──────────────────────────────────────────────────────

    @router.message(Command("add"))
    @inject
    async def cmd_add(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        parsed = await _resolve_user_and_number(command.args, user_repo)
        if parsed is None:
            await message.reply(formatter._t["admin_usage_add"])
            return
        target, amount = parsed
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        if amount <= 0:
            await message.reply(formatter._t["admin_negative_amount"])
            return
        new_value = await score_service.add_score(
            target.id, message.chat.id, amount, admin_id=message.from_user.id,
        )
        await message.reply(
            _admin_reply(formatter, target, new_value),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /sub ──────────────────────────────────────────────────────

    @router.message(Command("sub"))
    @inject
    async def cmd_sub(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        parsed = await _resolve_user_and_number(command.args, user_repo)
        if parsed is None:
            await message.reply(formatter._t["admin_usage_sub"])
            return
        target, amount = parsed
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        if amount <= 0:
            await message.reply(formatter._t["admin_negative_amount"])
            return
        new_value = await score_service.add_score(
            target.id, message.chat.id, -amount, admin_id=message.from_user.id,
        )
        await message.reply(
            _admin_reply(formatter, target, new_value),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /save ─────────────────────────────────────────────────────

    @router.message(Command("save"))
    @inject
    async def cmd_save(
        message: Message,
        command: CommandObject,
        user_repo: FromDishka[IUserRepository],
        saved_perms_repo: FromDishka[ISavedPermissionsRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None or message.bot is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        target = await _resolve_username(command.args, user_repo)
        if target is None:
            await message.reply(formatter._t["save_usage"])
            return
        display = user_link(target.username, target.full_name, target.id)
        try:
            member = await message.bot.get_chat_member(message.chat.id, target.id)
        except Exception:
            await message.reply(
                formatter._t["save_not_admin"].format(user=display),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        if not isinstance(member, ChatMemberAdministrator):
            await message.reply(
                formatter._t["save_not_admin"].format(user=display),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        perms = _extract_admin_permissions(member)
        existing = await saved_perms_repo.get(target.id, message.chat.id)
        await saved_perms_repo.save(target.id, message.chat.id, perms)
        key = "save_overwritten" if existing else "save_success"
        await message.reply(
            formatter._t[key].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /restore ──────────────────────────────────────────────────

    @router.message(Command("restore"))
    @inject
    async def cmd_restore(
        message: Message,
        command: CommandObject,
        user_repo: FromDishka[IUserRepository],
        saved_perms_repo: FromDishka[ISavedPermissionsRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None or message.bot is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        target = await _resolve_username(command.args, user_repo)
        if target is None:
            await message.reply(formatter._t["restore_usage"])
            return
        display = user_link(target.username, target.full_name, target.id)
        perms = await saved_perms_repo.get(target.id, message.chat.id)
        if perms is None:
            await message.reply(
                formatter._t["restore_not_found"].format(user=display),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        try:
            kw = _promote_kwargs(perms)
            await message.bot.promote_chat_member(
                chat_id=message.chat.id,
                user_id=target.id,
                **kw,
            )
            if perms.get("custom_title"):
                await message.bot.set_chat_administrator_custom_title(
                    chat_id=message.chat.id,
                    user_id=target.id,
                    custom_title=perms["custom_title"],
                )
        except Exception:
            logger.exception("Failed to restore permissions for user %d", target.id)
            await message.reply(formatter._t["restore_failed"])
            return
        await message.reply(
            formatter._t["restore_success"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /op ───────────────────────────────────────────────────────

    @router.message(Command("op"))
    @inject
    async def cmd_op(
        message: Message,
        command: CommandObject,
        user_repo: FromDishka[IUserRepository],
        saved_perms_repo: FromDishka[ISavedPermissionsRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None or message.bot is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        target = await _resolve_username(command.args, user_repo)
        if target is None:
            await message.reply(formatter._t["op_usage"])
            return
        display = user_link(target.username, target.full_name, target.id)
        try:
            member = await message.bot.get_chat_member(message.chat.id, target.id)
        except Exception:
            await message.reply(formatter._t["op_failed"])
            return
        if isinstance(member, ChatMemberOwner) or isinstance(member, ChatMemberAdministrator):
            await message.reply(
                formatter._t["op_already"].format(user=display),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        try:
            await message.bot.promote_chat_member(
                chat_id=message.chat.id,
                user_id=target.id,
                can_invite_users=True
            )
        except Exception:
            logger.exception("Failed to op user %d", target.id)
            await message.reply(formatter._t["op_failed"])
            return
        await saved_perms_repo.save(target.id, message.chat.id, MODERATOR_PERMS)
        await message.reply(
            formatter._t["op_success"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    @router.message(Command("deop"))
    @inject
    async def cmd_deop(
        message: Message,
        command: CommandObject,
        user_repo: FromDishka[IUserRepository],
        saved_perms_repo: FromDishka[ISavedPermissionsRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None or message.bot is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return
        target = await _resolve_username(command.args, user_repo)
        if target is None:
            await message.reply(formatter._t["op_usage"])
            return
        display = user_link(target.username, target.full_name, target.id)
        try:
            member = await message.bot.get_chat_member(message.chat.id, target.id)
        except Exception:
            await message.reply(formatter._t["op_failed"])
            return
        if isinstance(member, ChatMemberOwner):
            await message.reply(
                formatter._t["op_already"].format(user=display),  # нельзя снять owner-а
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        if not isinstance(member, ChatMemberAdministrator):
            await message.reply(
                formatter._t["deop_not_admin"].format(user=display),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        try:
            # Передаём все права как False — это снимает статус админа
            demote_kw = {f: False for f in ADMIN_PERM_FIELDS}
            await message.bot.promote_chat_member(
                chat_id=message.chat.id,
                user_id=target.id,
                **demote_kw,
            )
        except Exception:
            logger.exception("Failed to deop user %d", target.id)
            await message.reply(formatter._t["op_failed"])
            return
        await message.reply(
            formatter._t["deop_success"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    @router.message(Command("mute"))
    @inject
    async def cmd_mute(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        mute_service: FromDishka[MuteService],
        protection_repo: FromDishka[IMuteProtectionRepository],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None or message.bot is None:
            return

        mute_cfg = config.mute
        p = formatter._p

        parsed = await _resolve_mute_args(command.args, message, user_repo)
        if parsed is None:
            await message.reply(formatter._t["mute_usage"].format(
                min=mute_cfg.min_minutes, max=mute_cfg.max_minutes,
            ))
            return

        target, minutes = parsed
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        if target.id == message.from_user.id:
            await message.reply(formatter._t["mute_self"])
            return
        if minutes < mute_cfg.min_minutes or minutes > mute_cfg.max_minutes:
            await message.reply(formatter._t["mute_invalid_minutes"].format(
                min=mute_cfg.min_minutes, max=mute_cfg.max_minutes,
            ))
            return

        # Проверка защиты — работает против всех
        protected_until = await protection_repo.get(target.id, message.chat.id)
        if protected_until is not None:
            target_link = user_link(target.username, target.full_name, target.id)
            until_str = protected_until.astimezone(TZ_MSK).strftime("%H:%M %d.%m")
            await message.reply(
                formatter._t["mute_target_protected"].format(
                    target=target_link,
                    until=until_str,
                ),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return

        cost = minutes * mute_cfg.cost_per_minute
        score = await score_service.get_score(message.from_user.id, message.chat.id)
        if score.value < cost:
            await message.reply(formatter._t["mute_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ))
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
                demote_kw = {f: False for f in ADMIN_PERM_FIELDS}
                await bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **demote_kw)
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
                    kw = _promote_kwargs(admin_perms)
                    await bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **kw)
                    if admin_perms.get("custom_title"):
                        await bot.set_chat_administrator_custom_title(
                            chat_id=chat_id, user_id=target.id, custom_title=admin_perms["custom_title"],
                        )
                except Exception:
                    logger.exception("Failed to restore admin rights after mute failure")
            await message.reply(formatter._t["mute_failed"])
            return

        await mute_service.save_mute(MuteEntry(
            user_id=target.id,
            chat_id=chat_id,
            muted_by=message.from_user.id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        ))

        result = await score_service.spend_score(
            actor_id=message.from_user.id,
            target_id=target.id,
            chat_id=chat_id,
            cost=cost,
        )
        if not result.success:
            await _unmute_user(bot, mute_service, MuteEntry(
                user_id=target.id, chat_id=chat_id, muted_by=message.from_user.id,
                until_at=until, was_admin=was_admin, admin_permissions=admin_perms,
            ))
            await message.reply(formatter._t["mute_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ))
            return

        actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
        target_link = user_link(target.username, target.full_name, target.id)
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

    # ── /amute ────────────────────────────────────────────────────

    @router.message(Command("amute"))
    @inject
    async def cmd_amute(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        mute_service: FromDishka[MuteService],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        """Мут только для администраторов (конфиг или can_restrict_members в чате).
        Бесплатный, обходит защиту /protect."""
        if message.from_user is None or message.bot is None:
            return

        bot = message.bot
        chat_id = message.chat.id
        mute_cfg = config.mute

        # Проверка прав: конфиг-админ ИЛИ участник с can_restrict_members
        is_config_admin = _is_admin(message.from_user.username, config.admin.users)
        if not is_config_admin:
            try:
                caller_member = await bot.get_chat_member(chat_id, message.from_user.id)
                has_restrict = (
                    isinstance(caller_member, ChatMemberAdministrator)
                    and caller_member.can_restrict_members
                ) or isinstance(caller_member, ChatMemberOwner)
            except Exception:
                has_restrict = False
            if not has_restrict:
                await message.reply(formatter._t["amute_not_allowed"])
                return

        parsed = await _resolve_mute_args(command.args, message, user_repo)
        if parsed is None:
            await message.reply(formatter._t["amute_usage"].format(
                min=mute_cfg.min_minutes, max=mute_cfg.max_minutes,
            ))
            return

        target, minutes = parsed
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        if target.id == message.from_user.id:
            await message.reply(formatter._t["mute_self"])
            return
        if minutes < 1:
            await message.reply(formatter._t["mute_invalid_minutes"].format(
                min=mute_cfg.min_minutes, max=mute_cfg.max_minutes,
            ))
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
                demote_kw = {f: False for f in ADMIN_PERM_FIELDS}
                await bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **demote_kw)
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
                    kw = _promote_kwargs(admin_perms)
                    await bot.promote_chat_member(chat_id=chat_id, user_id=target.id, **kw)
                    if admin_perms.get("custom_title"):
                        await bot.set_chat_administrator_custom_title(
                            chat_id=chat_id, user_id=target.id, custom_title=admin_perms["custom_title"],
                        )
                except Exception:
                    logger.exception("Failed to restore admin rights after amute failure")
            await message.reply(formatter._t["mute_failed"])
            return

        await mute_service.save_mute(MuteEntry(
            user_id=target.id,
            chat_id=chat_id,
            muted_by=message.from_user.id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        ))

        actor_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
        target_link = user_link(target.username, target.full_name, target.id)
        await message.reply(
            formatter._t["amute_success"].format(
                actor=actor_link,
                target=target_link,
                minutes=minutes,
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /selfmute ─────────────────────────────────────────────────

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
            await message.reply(formatter._t["selfmute_usage"].format(
                min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes,
            ))
            return

        seconds = _parse_duration(command.args)
        if seconds is None:
            await message.reply(formatter._t["selfmute_usage"].format(
                min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes,
            ))
            return

        if seconds <= 0 or seconds < min_sec or seconds > max_sec:
            await message.reply(formatter._t["selfmute_invalid_minutes"].format(
                min=mute_cfg.selfmute_min_minutes, max=mute_cfg.selfmute_max_minutes,
            ))
            return

        bot = message.bot
        chat_id = message.chat.id
        user_id = message.from_user.id
        until = datetime.now(TZ_MSK) + timedelta(seconds=seconds)

        # Проверяем статус — админа нельзя замутить напрямую, нужно сначала снять права
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
                demote_kw = {f: False for f in ADMIN_PERM_FIELDS}
                await bot.promote_chat_member(chat_id=chat_id, user_id=user_id, **demote_kw)
        except TelegramBadRequest as e:
            logger.warning("selfmute pre-check failed for user %d: %s", user_id, e)
            await message.reply(formatter._t["selfmute_failed"])
            return
        except Exception:
            logger.exception("selfmute pre-check failed for user %d", user_id)
            await message.reply(formatter._t["selfmute_failed"])
            return

        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
        except TelegramBadRequest as e:
            logger.warning("restrict_chat_member failed for selfmute user %d: %s", user_id, e)
            if was_admin and admin_perms:
                try:
                    await bot.promote_chat_member(chat_id=chat_id, user_id=user_id, **_promote_kwargs(admin_perms))
                except Exception:
                    logger.exception("Failed to restore admin rights after selfmute failure for user %d", user_id)
            await message.reply(formatter._t["selfmute_failed"])
            return
        except Exception:
            logger.exception("restrict_chat_member failed for selfmute user %d", user_id)
            if was_admin and admin_perms:
                try:
                    await bot.promote_chat_member(chat_id=chat_id, user_id=user_id, **_promote_kwargs(admin_perms))
                except Exception:
                    logger.exception("Failed to restore admin rights after selfmute failure for user %d", user_id)
            await message.reply(formatter._t["selfmute_failed"])
            return

        await mute_service.save_mute(MuteEntry(
            user_id=user_id,
            chat_id=chat_id,
            muted_by=user_id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        ))

        duration_str = _format_duration(seconds)
        user_link_str = user_link(message.from_user.username, message.from_user.full_name or "", user_id)
        await message.reply(
            formatter._t["selfmute_success"].format(user=user_link_str, duration=duration_str),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /unmute ───────────────────────────────────────────────────

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
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return

        # Поддержка reply: /unmute без аргументов на сообщение пользователя
        target = await _resolve_username(command.args, user_repo)
        if target is None:
            reply = message.reply_to_message
            if reply is not None and reply.from_user is not None:
                from bot.domain.entities import User as DomainUser
                tg_user = reply.from_user
                target = DomainUser(
                    id=tg_user.id,
                    username=tg_user.username,
                    full_name=tg_user.full_name or str(tg_user.id),
                )
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

    # ── /tag ──────────────────────────────────────────────────────

    @router.message(Command("tag"))
    @inject
    async def cmd_tag(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None or message.bot is None:
            return

        tc = config.tag
        p = formatter._p
        bot = message.bot
        chat_id = message.chat.id

        args = command.args
        if not args:
            await message.reply(formatter._t["tag_usage"].format(
                cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self),
            ))
            return

        parts = args.strip().split(maxsplit=1)
        if parts[0].startswith("@"):
            username = parts[0].lstrip("@")
            target = await user_repo.get_by_username(username)
            if target is None:
                await message.reply(formatter._t["error_user_not_found"])
                return
            new_tag = parts[1].strip() if len(parts) > 1 else None
            if new_tag is None:
                await message.reply(formatter._t["tag_usage"].format(
                    cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self),
                ))
                return
            is_self = (target.id == message.from_user.id)
        else:
            target = await user_repo.get_by_id(message.from_user.id)
            if target is None:
                await message.reply(formatter._t["error_user_not_found"])
                return
            new_tag = args.strip()
            is_self = True

        clearing = (new_tag == "--clear")
        is_free = _is_admin(message.from_user.username, config.admin.users)

        if not clearing and len(new_tag) > tc.max_length:
            await message.reply(formatter._t["tag_too_long"].format(max=tc.max_length))
            return

        if is_self:
            cost = tc.cost_self
        else:
            try:
                member = await bot.get_chat_member(chat_id, target.id)
            except Exception:
                await message.reply(formatter._t["tag_failed"])
                return
            if isinstance(member, ChatMemberOwner):
                cost = tc.cost_owner
            elif isinstance(member, ChatMemberAdministrator):
                cost = tc.cost_admin
            else:
                cost = tc.cost_member

        if not is_free:
            score = await score_service.get_score(message.from_user.id, chat_id)
            if score.value < cost:
                await message.reply(formatter._t["tag_not_enough"].format(
                    cost=cost,
                    score_word=p.pluralize(cost),
                    balance=score.value,
                    score_word_balance=p.pluralize(score.value),
                ))
                return

        try:
            await bot.set_chat_member_tag(
                chat_id=chat_id,
                user_id=target.id,
                tag=None if clearing else new_tag,
            )
        except Exception:
            await message.reply(formatter._t["tag_failed"])
            return

        target_link = user_link(target.username, target.full_name, target.id)

        if is_free:
            text = (
                formatter._t["tag_cleared_free"].format(target=target_link)
                if clearing
                else formatter._t["tag_success_free"].format(target=target_link, tag=new_tag)
            )
            await message.reply(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
            return

        result = await score_service.spend_score(
            actor_id=message.from_user.id,
            target_id=target.id,
            chat_id=chat_id,
            cost=cost,
            emoji=SPECIAL_EMOJI["tag"],
        )
        if not result.success:
            await message.reply(formatter._t["tag_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ))
            return

        if clearing:
            text = formatter._t["tag_cleared"].format(
                target=target_link,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.new_balance,
                score_word_balance=p.pluralize(result.new_balance),
            )
        else:
            text = formatter._t["tag_success"].format(
                target=target_link,
                tag=new_tag,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.new_balance,
                score_word_balance=p.pluralize(result.new_balance),
            )
        await message.reply(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)

    # ── /transfer ─────────────────────────────────────────────────

    @router.message(Command("transfer"))
    @inject
    async def cmd_transfer(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        user_repo: FromDishka[IUserRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None:
            return

        p = formatter._p
        chat_id = message.chat.id

        parsed = await _resolve_user_and_number(command.args, user_repo)
        if parsed is None:
            await message.reply(formatter._t["transfer_usage"])
            return

        target, amount = parsed

        if amount <= 0:
            await message.reply(formatter._t["transfer_invalid_amount"])
            return

        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return

        if target.id == message.from_user.id:
            await message.reply(formatter._t["transfer_self"])
            return

        result = await score_service.transfer_score(
            sender_id=message.from_user.id,
            receiver_id=target.id,
            chat_id=chat_id,
            amount=amount,
        )

        if not result.success:
            await message.reply(
                formatter._t["transfer_not_enough"].format(
                    amount=amount,
                    score_word=p.pluralize(amount),
                    balance=result.current_balance,
                    score_word_balance=p.pluralize(result.current_balance),
                )
            )
            return

        sender_link = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
        receiver_link = user_link(target.username, target.full_name, target.id)

        await message.reply(
            formatter._t["transfer_success"].format(
                sender=sender_link,
                receiver=receiver_link,
                amount=amount,
                score_word=p.pluralize(amount),
                sender_balance=result.sender_balance,
                score_word_sender=p.pluralize(result.sender_balance),
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── /protect ──────────────────────────────────────────────────

    @router.message(Command("protect"))
    @inject
    async def cmd_protect(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None:
            return

        mute_cfg = config.mute
        p = formatter._p
        chat_id = message.chat.id
        user_id = message.from_user.id
        cost = mute_cfg.protection_cost
        hours = mute_cfg.protection_duration_hours

        score = await score_service.get_score(user_id, chat_id)
        if score.value < cost:
            await message.reply(formatter._t["protect_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ))
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"✅ Да, потратить {cost} {p.pluralize(cost)}",
                callback_data=f"protect:confirm:{user_id}:{chat_id}",
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"protect:cancel:{user_id}",
            ),
        ]])
        await message.reply(
            formatter._t["protect_confirm"].format(
                hours=hours,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=kb,
        )

    @router.callback_query(F.data.startswith("protect:"))
    @inject
    async def cb_protect(
        callback: CallbackQuery,
        score_service: FromDishka[ScoreService],
        protection_repo: FromDishka[IMuteProtectionRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        async def safe_answer(text: str = "", alert: bool = False) -> None:
            try:
                await callback.answer(text, show_alert=alert)
            except Exception:
                pass

        parts = callback.data.split(":")
        action = parts[1]

        # Только тот, кто вызвал /protect
        owner_id = int(parts[2])
        if callback.from_user.id != owner_id:
            await safe_answer("Это не твоя кнопка.", alert=True)
            return

        if action == "cancel":
            try:
                await callback.message.edit_text("❌ Защита отменена.")
            except Exception:
                pass
            await safe_answer()
            return

        # action == "confirm"
        chat_id = int(parts[3])
        mute_cfg = config.mute
        p = formatter._p
        cost = mute_cfg.protection_cost
        hours = mute_cfg.protection_duration_hours
        user_id = owner_id

        result = await score_service.spend_score(
            actor_id=user_id,
            target_id=user_id,
            chat_id=chat_id,
            cost=cost,
            emoji=SPECIAL_EMOJI["protect"],
        )
        if not result.success:
            try:
                await callback.message.edit_text(
                    formatter._t["protect_not_enough"].format(
                        cost=cost,
                        score_word=p.pluralize(cost),
                        balance=result.current_balance,
                        score_word_balance=p.pluralize(result.current_balance),
                    )
                )
            except Exception:
                pass
            await safe_answer()
            return

        existing = await protection_repo.get(user_id, chat_id)
        now = datetime.now(TZ_MSK)
        new_until = (existing if existing and existing > now else now) + timedelta(hours=hours)
        await protection_repo.save(user_id, chat_id, new_until)

        until_str = new_until.strftime("%H:%M %d.%m")
        user_link_str = user_link(callback.from_user.username, callback.from_user.full_name or "", user_id)

        if existing and existing > now:
            text = formatter._t["protect_extended"].format(
                until=until_str,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.new_balance,
                score_word_balance=p.pluralize(result.new_balance),
            )
        else:
            text = formatter._t["protect_success"].format(
                user=user_link_str,
                hours=hours,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.new_balance,
                score_word_balance=p.pluralize(result.new_balance),
            )

        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await safe_answer()

    # ── /help ─────────────────────────────────────────────────────

    @router.message(Command("help"))
    @inject
    async def cmd_help(
        message: Message,
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None:
            return
        uid = message.from_user.id
        await message.reply(
            _help_main_text(config.score.icon),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=_help_main_kb(uid),
        )

    # ── Callback для /help (только вызвавший) ─────────────────────

    @router.callback_query(F.data.startswith("help:"))
    @inject
    async def cb_help(
        callback: CallbackQuery,
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        async def safe_answer(text: str = "", show_alert: bool = False) -> None:
            try:
                await callback.answer(text, show_alert=show_alert)
            except Exception:
                pass

        # Формат: "help:{section}:{caller_uid}"
        parts = callback.data.split(":")
        if len(parts) != 3:
            await safe_answer()
            return

        _, section, caller_uid_str = parts

        try:
            caller_uid = int(caller_uid_str)
        except ValueError:
            await safe_answer()
            return

        # Только тот, кто вызвал /help, может нажимать кнопки
        if callback.from_user.id != caller_uid:
            await safe_answer("Это не твоя справка.", show_alert=True)
            return

        uid = caller_uid

        if section == "main":
            text = _help_main_text(config.score.icon)
            kb = _help_main_kb(uid)
        else:
            text = _help_section_text(section, config, formatter)
            kb = _help_back_kb(uid)

        if not text:
            await safe_answer()
            return

        try:
            await callback.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                link_preview_options=NO_PREVIEW,
            )
        except Exception:
            pass

        await safe_answer()

    return router