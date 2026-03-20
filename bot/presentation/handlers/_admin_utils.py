"""Общие вспомогательные функции для обработчиков команд."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import ChatMemberAdministrator, ChatPermissions, Message

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.domain.bot_utils import parse_duration
from bot.domain.entities import MuteEntry
from bot.infrastructure.message_formatter import MessageFormatter, user_link

logger = logging.getLogger(__name__)

# Поля прав администратора (Telegram API)
_ADMIN_PERM_FIELDS = (
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

# Права модератора, выдаваемые через /op
MODERATOR_PERMS = {
    "can_delete_messages": False,
    "can_restrict_members": False,
    "can_invite_users": True,
    "can_pin_messages": False,
    "can_manage_video_chats": False,
    "can_manage_topics": False,
}


def _extract_admin_permissions(member: ChatMemberAdministrator) -> dict:
    perms: dict = {}
    for field in _ADMIN_PERM_FIELDS:
        perms[field] = getattr(member, field, False) or False
    if member.custom_title:
        perms["custom_title"] = member.custom_title
    return perms


def _promote_kwargs(perms: dict) -> dict:
    return {k: v for k, v in perms.items() if k in _ADMIN_PERM_FIELDS}


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


async def _resolve_mute_args(args, message: Message, user_repo: IUserRepository):
    """Разбирает аргументы /mute и /amute с поддержкой reply.

    Варианты:
      /mute @username 30    — явный username + время
      /mute 30              — только время, цель берётся из reply
    """
    from bot.domain.entities import User as DomainUser

    args_str = (args or "").strip()
    parts = args_str.split()

    if len(parts) == 2:
        username = parts[0].lstrip("@")
        seconds = parse_duration(parts[1])
        if seconds is not None:
            user = await user_repo.get_by_username(username)
            return (user, max(1, seconds // 60))
        return None

    if len(parts) == 1:
        seconds = parse_duration(parts[0])
        if seconds is None:
            return None
        reply = message.reply_to_message
        if reply is None or reply.from_user is None:
            return None
        tg_user = reply.from_user
        target = DomainUser(
            id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name or str(tg_user.id),
        )
        return (target, max(1, seconds // 60))

    return None


async def _resolve_username(args: str | None, user_repo: IUserRepository):
    if not args:
        return None
    return await user_repo.get_by_username(args.strip().lstrip("@"))


def _admin_reply(formatter: MessageFormatter, target, new_value: int) -> str:
    display = user_link(target.username, target.full_name, target.id)
    return formatter._t["admin_score_set"].format(
        user=display,
        total=new_value,
        score_word=formatter._p.pluralize(abs(new_value)),
    )


async def _unmute_user(bot: Bot, mute_service: MuteService, entry: MuteEntry) -> None:
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
                entry.user_id,
                entry.chat_id,
            )

    await mute_service.delete_mute(entry.user_id, entry.chat_id)
