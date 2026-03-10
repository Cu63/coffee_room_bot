"""Команды с динамическим префиксом из конфига.

Включает:
- Админские: /{prefix}_add, /{prefix}_sub, /{prefix}_set, /{prefix}_reset
- Админские: /{prefix}_save, /{prefix}_restore (управление правами)
- Пользовательские: /{prefix}_mute, /{prefix}_tag
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    ChatMemberAdministrator,
    ChatMemberOwner,
    ChatPermissions,
    LinkPreviewOptions,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import ScoreService, SPECIAL_EMOJI
from bot.application.mute_service import MuteService
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.saved_permissions_repository import ISavedPermissionsRepository
from bot.domain.entities import MuteEntry
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Права администратора, которые мы сохраняем/восстанавливаем
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


def _extract_admin_permissions(member: ChatMemberAdministrator) -> dict:
    """Извлекает текущие права админа в dict для сохранения в БД."""
    perms: dict = {}
    for field in ADMIN_PERM_FIELDS:
        perms[field] = getattr(member, field, False) or False
    if member.custom_title:
        perms["custom_title"] = member.custom_title
    return perms


def _promote_kwargs(perms: dict) -> dict:
    """Фильтрует dict прав: оставляет только валидные параметры promote_chat_member."""
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


def _admin_reply(formatter: MessageFormatter, target, new_value: int) -> str:
    display = user_link(target.username, target.full_name, target.id)
    return formatter._t["admin_score_set"].format(
        user=display,
        total=new_value,
        score_word=formatter._p.pluralize(abs(new_value)),
    )


def _usage(formatter: MessageFormatter, key: str, **kwargs) -> str:
    return formatter._t[key].format(**kwargs)


async def _resolve_username(args: str | None, user_repo: IUserRepository):
    """Парсит @username из аргументов. Возвращает User | None."""
    if not args:
        return None
    username = args.strip().lstrip("@")
    return await user_repo.get_by_username(username)


def create_admin_router(prefix: str) -> Router:
    """Создаёт роутер с командами на основе префикса."""

    router = Router(name="admin_commands")

    # ── Админские команды: баллы ──────────────────────────────────

    @router.message(Command(f"{prefix}_reset"))
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
            await message.reply(_usage(formatter, "admin_usage_reset", prefix=prefix))
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

    @router.message(Command(f"{prefix}_set"))
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
            await message.reply(_usage(formatter, "admin_usage_set", prefix=prefix))
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

    @router.message(Command(f"{prefix}_add"))
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
            await message.reply(_usage(formatter, "admin_usage_add", prefix=prefix))
            return

        target, amount = parsed
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return

        new_value = await score_service.add_score(
            target.id, message.chat.id, amount, admin_id=message.from_user.id,
        )
        await message.reply(
            _admin_reply(formatter, target, new_value),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    @router.message(Command(f"{prefix}_sub"))
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
            await message.reply(_usage(formatter, "admin_usage_sub", prefix=prefix))
            return

        target, amount = parsed
        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return

        new_value = await score_service.add_score(
            target.id, message.chat.id, -amount, admin_id=message.from_user.id,
        )
        await message.reply(
            _admin_reply(formatter, target, new_value),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── Админские команды: управление правами ─────────────────────

    @router.message(Command(f"{prefix}_save"))
    @inject
    async def cmd_save(
        message: Message,
        command: CommandObject,
        user_repo: FromDishka[IUserRepository],
        saved_perms_repo: FromDishka[ISavedPermissionsRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        """Сохраняет текущие права администратора в БД."""
        if message.from_user is None or message.bot is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return

        target = await _resolve_username(command.args, user_repo)
        if target is None:
            await message.reply(_usage(formatter, "save_usage", prefix=prefix))
            return

        display = user_link(target.username, target.full_name, target.id)

        # Проверяем, что пользователь — админ в чате
        try:
            member = await message.bot.get_chat_member(message.chat.id, target.id)
        except Exception:
            await message.reply(formatter._t["save_not_admin"].format(user=display), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
            return

        if not isinstance(member, ChatMemberAdministrator):
            await message.reply(formatter._t["save_not_admin"].format(user=display), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
            return

        perms = _extract_admin_permissions(member)

        # Проверяем, были ли уже сохранены права
        existing = await saved_perms_repo.get(target.id, message.chat.id)
        await saved_perms_repo.save(target.id, message.chat.id, perms)

        key = "save_overwritten" if existing else "save_success"
        await message.reply(
            formatter._t[key].format(user=display, prefix=prefix),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    @router.message(Command(f"{prefix}_restore"))
    @inject
    async def cmd_restore(
        message: Message,
        command: CommandObject,
        user_repo: FromDishka[IUserRepository],
        saved_perms_repo: FromDishka[ISavedPermissionsRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        """Восстанавливает сохранённые права — теперь назначены ботом."""
        if message.from_user is None or message.bot is None:
            return
        if not _is_admin(message.from_user.username, config.admin.users):
            await message.reply(formatter._t["admin_not_allowed"])
            return

        target = await _resolve_username(command.args, user_repo)
        if target is None:
            await message.reply(_usage(formatter, "restore_usage", prefix=prefix))
            return

        display = user_link(target.username, target.full_name, target.id)

        perms = await saved_perms_repo.get(target.id, message.chat.id)
        if perms is None:
            await message.reply(
                formatter._t["restore_not_found"].format(user=display, prefix=prefix),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return

        try:
            # Назначаем права ботом — теперь бот сможет их снимать
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

        # Не удаляем из saved_permissions — пригодится для повторных restore
        await message.reply(
            formatter._t["restore_success"].format(user=display),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── Мут (доступен всем пользователям) ─────────────────────────

    @router.message(Command(f"{prefix}_mute"))
    @inject
    async def cmd_mute(
        message: Message,
        command: CommandObject,
        score_service: FromDishka[ScoreService],
        mute_service: FromDishka[MuteService],
        user_repo: FromDishka[IUserRepository],
        saved_perms_repo: FromDishka[ISavedPermissionsRepository],
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        if message.from_user is None or message.bot is None:
            return

        mute_cfg = config.mute
        p = formatter._p

        # Парсинг аргументов
        parsed = await _resolve_user_and_number(command.args, user_repo)
        if parsed is None:
            await message.reply(_usage(
                formatter, "mute_usage",
                prefix=prefix,
                min=mute_cfg.min_minutes,
                max=mute_cfg.max_minutes,
            ))
            return

        target, minutes = parsed

        if target is None:
            await message.reply(formatter._t["error_user_not_found"])
            return

        # Нельзя мутить самого себя
        if target.id == message.from_user.id:
            await message.reply(formatter._t["mute_self"])
            return

        # Валидация времени
        if minutes < mute_cfg.min_minutes or minutes > mute_cfg.max_minutes:
            await message.reply(formatter._t["mute_invalid_minutes"].format(
                min=mute_cfg.min_minutes,
                max=mute_cfg.max_minutes,
            ))
            return

        cost = minutes * mute_cfg.cost_per_minute

        # Проверка баланса
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
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)

        # Определяем статус пользователя в чате
        try:
            member = await bot.get_chat_member(chat_id, target.id)
        except Exception:
            await message.reply(formatter._t["mute_failed"])
            return

        was_admin = isinstance(member, ChatMemberAdministrator)
        admin_perms: dict | None = None

        if was_admin:
            admin_perms = _extract_admin_permissions(member)

            # Пробуем понизить (сработает только если бот назначил этого админа)
            try:
                demote_kw = {f: False for f in ADMIN_PERM_FIELDS}
                await bot.promote_chat_member(
                    chat_id=chat_id,
                    user_id=target.id,
                    **demote_kw,
                )
            except Exception:
                await message.reply(formatter._t["mute_failed"])
                return

        # Рестрикт: запрет отправки сообщений
        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=target.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
        except Exception:
            # Откат: вернуть админские права если были
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

        # Сохраняем мут в БД для восстановления прав по таймеру
        await mute_service.save_mute(MuteEntry(
            user_id=target.id,
            chat_id=chat_id,
            muted_by=message.from_user.id,
            until_at=until,
            was_admin=was_admin,
            admin_permissions=admin_perms,
        ))

        # Списываем баллы
        result = await score_service.spend_score(
            actor_id=message.from_user.id,
            target_id=target.id,
            chat_id=chat_id,
            cost=cost,
        )

        if not result.success:
            # Гонка: баланс изменился — откатываем мут
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

        actor_link = user_link(
            message.from_user.username,
            message.from_user.full_name or "",
            message.from_user.id,
        )
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

    # ── Смена тега (доступна всем пользователям) ──────────────────

    @router.message(Command(f"{prefix}_tag"))
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

        tag_cfg = config.tag
        p = formatter._p
        args = (command.args or "").strip()

        # Парсинг: /prefix_tag new_tag | /prefix_tag @user new_tag | /prefix_tag @user
        target_user = None
        new_tag = ""
        is_self = True

        if not args:
            # /coffee_tag — показать usage
            await message.reply(formatter._t["tag_usage"].format(
                prefix=prefix,
                cost_self=tag_cfg.cost_self,
                sw_self=p.pluralize(tag_cfg.cost_self),
            ))
            return

        parts = args.split(maxsplit=1)

        if parts[0].startswith("@"):
            # /coffee_tag @user [new_tag]
            username = parts[0].lstrip("@")
            target_user = await user_repo.get_by_username(username)
            if target_user is None:
                await message.reply(formatter._t["error_user_not_found"])
                return
            new_tag = parts[1] if len(parts) > 1 else ""
            is_self = target_user.id == message.from_user.id
        else:
            # /coffee_tag new_tag (для себя)
            new_tag = args

        # Валидация длины
        if len(new_tag) > tag_cfg.max_length:
            await message.reply(formatter._t["tag_too_long"].format(max=tag_cfg.max_length))
            return

        target_id = target_user.id if target_user else message.from_user.id
        chat_id = message.chat.id
        bot = message.bot

        # Определяем роль цели для расчёта стоимости
        if is_self:
            cost = tag_cfg.cost_self
        else:
            try:
                member = await bot.get_chat_member(chat_id, target_id)
            except Exception:
                cost = tag_cfg.cost_member
                member = None

            if isinstance(member, ChatMemberOwner):
                cost = tag_cfg.cost_owner
            elif isinstance(member, ChatMemberAdministrator):
                cost = tag_cfg.cost_admin
            else:
                cost = tag_cfg.cost_member

        # Проверка баланса
        score = await score_service.get_score(message.from_user.id, chat_id)
        if score.value < cost:
            await message.reply(formatter._t["tag_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ))
            return

        # Устанавливаем тег через Telegram API
        try:
            await bot.set_chat_member_tag(
                chat_id=chat_id,
                user_id=target_id,
                tag=new_tag,
            )
        except Exception:
            await message.reply(formatter._t["tag_failed"])
            return

        # Списываем баллы
        result = await score_service.spend_score(
            actor_id=message.from_user.id,
            target_id=target_id,
            chat_id=chat_id,
            cost=cost,
            emoji=SPECIAL_EMOJI["tag"],
        )

        if not result.success:
            # Гонка — откат тега невозможен, но баллов уже не хватает
            await message.reply(formatter._t["tag_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.current_balance,
                score_word_balance=p.pluralize(result.current_balance),
            ))
            return

        actor_link = user_link(
            message.from_user.username,
            message.from_user.full_name or "",
            message.from_user.id,
        )
        if target_user:
            target_link = user_link(target_user.username, target_user.full_name, target_user.id)
        else:
            target_link = actor_link

        if new_tag:
            reply_key = "tag_success"
        else:
            reply_key = "tag_reset_success"

        await message.reply(
            formatter._t[reply_key].format(
                actor=actor_link,
                target=target_link,
                tag=new_tag,
                cost=cost,
                score_word=p.pluralize(cost),
                balance=result.new_balance,
                score_word_balance=p.pluralize(result.new_balance),
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )

    # ── Смена тега (доступна всем) ────────────────────────────────

    @router.message(Command(f"{prefix}_tag"))
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

        # Парсинг: /prefix_tag [@user] new_tag | --clear
        args = command.args
        if not args:
            await message.reply(formatter._t["tag_usage"].format(
                prefix=prefix, cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self),
            ))
            return

        parts = args.strip().split(maxsplit=1)
        if parts[0].startswith("@"):
            # Тег для другого
            username = parts[0].lstrip("@")
            target = await user_repo.get_by_username(username)
            if target is None:
                await message.reply(formatter._t["error_user_not_found"])
                return
            new_tag = parts[1].strip() if len(parts) > 1 else None
            if new_tag is None:
                await message.reply(formatter._t["tag_usage"].format(
                    prefix=prefix, cost_self=tc.cost_self, sw_self=p.pluralize(tc.cost_self),
                ))
                return
            is_self = (target.id == message.from_user.id)
        else:
            # Тег для себя
            target = await user_repo.get_by_id(message.from_user.id)
            if target is None:
                await message.reply(formatter._t["error_user_not_found"])
                return
            new_tag = args.strip()
            is_self = True

        clearing = (new_tag == "--clear")

        # Определяем стоимость по роли
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

        # Проверка баланса
        score = await score_service.get_score(message.from_user.id, chat_id)
        if score.value < cost:
            await message.reply(formatter._t["tag_not_enough"].format(
                cost=cost,
                score_word=p.pluralize(cost),
                balance=score.value,
                score_word_balance=p.pluralize(score.value),
            ))
            return

        # Применяем тег
        try:
            await bot.set_chat_member_tag(
                chat_id=chat_id,
                user_id=target.id,
                tag=None if clearing else new_tag,
            )
        except Exception:
            await message.reply(formatter._t["tag_failed"])
            return

        # Списываем
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

        target_link = user_link(target.username, target.full_name, target.id)

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

    # ── Справка (доступна всем) ───────────────────────────────────

    @router.message(Command(f"{prefix}_help"))
    @inject
    async def cmd_help(
        message: Message,
        formatter: FromDishka[MessageFormatter],
        config: FromDishka[AppConfig],
    ) -> None:
        p = formatter._p
        mc = config.mute
        tc = config.tag
        lc = config.limits
        icon = config.score.icon

        # Реакции
        reactions_lines = []
        for emoji, weight in config.reactions.items():
            sign = f"+{weight}" if weight > 0 else str(weight)
            reactions_lines.append(f"  {emoji} → {sign} {p.pluralize(abs(weight))}")
        reactions_block = "\n".join(reactions_lines)

        # Команды
        commands_block = (
            f"  /score — твой счёт\n"
            f"  /score @user — счёт пользователя\n"
            f"  /top [N] — таблица лидеров\n"
            f"  /history — история начислений\n"
            f"  /{prefix}_mute @user N — мут (N мин, {mc.min_minutes}–{mc.max_minutes})\n"
            f"  /{prefix}_tag [тег] — сменить свой тег\n"
            f"  /{prefix}_tag @user [тег] — сменить чужой тег\n"
            f"  /{prefix}_help — эта справка"
        )

        # Админские команды
        admin_block = (
            f"  /{prefix}_add @user N\n"
            f"  /{prefix}_sub @user N\n"
            f"  /{prefix}_set @user N\n"
            f"  /{prefix}_reset @user\n"
            f"  /{prefix}_save @user\n"
            f"  /{prefix}_restore @user"
        )

        text = (
            f"{icon} <b>Как работает бот</b>\n"
            f"\n"
            f"Ставь реакции на сообщения — автор получит или потеряет баллы.\n"
            f"\n"
            f"<b>Реакции:</b>\n"
            f"{reactions_block}\n"
            f"\n"
            f"<b>Лимиты:</b>\n"
            f"  Реакций в сутки: {lc.daily_reactions_given}\n"
            f"  Макс. баллов получателю в сутки: {lc.daily_score_received}\n"
            f"  Реакции на сообщения старше {lc.max_message_age_hours} ч. не учитываются\n"
            f"  Негативные реакции от участников с отрицательным счётом игнорируются\n"
            f"  История хранится {config.history.retention_days} дн.\n"
            f"\n"
            f"<b>Мут:</b>\n"
            f"  Стоимость: {mc.cost_per_minute} {p.pluralize(mc.cost_per_minute)} / мин\n"
            f"  Длительность: {mc.min_minutes}–{mc.max_minutes} мин\n"
            f"  Баллы в долг не даются\n"
            f"\n"
            f"<b>Смена тега:</b>\n"
            f"  Себе: {tc.cost_self} {p.pluralize(tc.cost_self)}\n"
            f"  Участнику: {tc.cost_member} {p.pluralize(tc.cost_member)}\n"
            f"  Админу: {tc.cost_admin} {p.pluralize(tc.cost_admin)}\n"
            f"  Создателю: {tc.cost_owner} {p.pluralize(tc.cost_owner)}\n"
            f"  --clear для удаления тега\n"
            f"\n"
            f"<b>Команды:</b>\n"
            f"{commands_block}\n"
            f"\n"
            f"<b>Админ:</b>\n"
            f"{admin_block}"
        )

        await message.reply(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)

    return router


async def _unmute_user(bot, mute_service: MuteService, entry: MuteEntry) -> None:
    """Снимает мут: восстанавливает права и удаляет запись из БД."""
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

    # Восстанавливаем админские права если были
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