"""Предупреждения: /warn, /warns, /unwarn. Выдают и снимают те, у кого есть право на муты."""

from __future__ import annotations

import html

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.mute_service import MuteService
from bot.application.warn_service import WarnService
from bot.domain.entities import User
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.handlers._admin_utils import MuteOutcome, apply_mute, can_mute
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="warn")


async def _target(args: str | None, message: Message, user_repo: IUserRepository) -> tuple[User | None, str, bool]:
    """Цель и остаток аргументов: «@username …» или ответом на сообщение.

    Третье значение — цель назвали по имени: если её нет в базе, это «не найден», а не «как пользоваться».
    """
    text = (args or "").strip()
    if text.startswith("@"):
        name, _, rest = text.partition(" ")
        return await user_repo.get_by_username(name.lstrip("@")), rest.strip(), True
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None and not reply.from_user.is_bot:
        tg = reply.from_user
        return User(id=tg.id, username=tg.username, full_name=tg.full_name or str(tg.id)), text, False
    return None, text, False


def _link(user: User) -> str:
    return user_link(user.username, user.full_name, user.id)


async def _reply(message: Message, text: str) -> None:
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


@router.message(Command("warn"))
@inject
async def cmd_warn(
    message: Message,
    command: CommandObject,
    warn_service: FromDishka[WarnService],
    mute_service: FromDishka[MuteService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/warn @user причина или ответом — предупреждение; набрал лимит — автомут."""
    if message.from_user is None or message.bot is None:
        return
    bot = message.bot
    chat_id = message.chat.id
    if not await can_mute(bot, chat_id, message.from_user, config.admin.users):
        await reply_and_delete(message, formatter._t["mute_not_allowed"])
        return
    target, reason, named = await _target(command.args, message, user_repo)
    if target is None:
        await reply_and_delete(message, formatter._t["error_user_not_found" if named else "warn_usage"])
        return
    if target.id == message.from_user.id:
        await reply_and_delete(message, formatter._t["warn_self"])
        return

    result = await warn_service.warn(chat_id, target.id, message.from_user.id, reason)
    text = formatter._t["warn_issued"].format(target=_link(target), count=result.count, limit=result.limit)
    if reason:
        text += formatter._t["warn_reason"].format(reason=html.escape(reason))
    if result.reached:
        cfg = config.warn
        until, _ = await mute_service.compute_stacked_until(target.id, chat_id, cfg.mute_minutes * 60)
        muted = await apply_mute(
            bot, mute_service, target_id=target.id, chat_id=chat_id, muted_by=message.from_user.id, until=until
        )
        if muted.outcome == MuteOutcome.MUTED:
            # Предупреждения «сгорают»: за них выдан мут
            await warn_service.clear(chat_id, target.id)
            text += "\n" + formatter._t["warn_muted"].format(minutes=cfg.mute_minutes)
        else:
            text += "\n" + formatter._t["warn_mute_impossible"]
    await _reply(message, text)


@router.message(Command("warns"))
@inject
async def cmd_warns(
    message: Message,
    command: CommandObject,
    warn_service: FromDishka[WarnService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/warns — свои предупреждения; /warns @user или ответом — чужие."""
    if message.from_user is None:
        return
    target, _, named = await _target(command.args, message, user_repo)
    if target is None:
        if named:
            await reply_and_delete(message, formatter._t["error_user_not_found"])
            return
        me = message.from_user
        target = User(id=me.id, username=me.username, full_name=me.full_name or str(me.id))

    entries = await warn_service.active(message.chat.id, target.id)
    if not entries:
        await _reply(message, formatter._t["warns_none"].format(target=_link(target)))
        return
    issuers = {uid: await user_repo.get_by_id(uid) for uid in {entry.issued_by for entry in entries}}
    cfg = config.warn
    lines = [
        formatter._t["warns_header"].format(
            target=_link(target), count=len(entries), limit=cfg.limit, days=cfg.period_days
        )
    ]
    for n, entry in enumerate(entries, 1):
        issuer = issuers.get(entry.issued_by)
        lines.append(
            formatter._t["warns_row"].format(
                n=n,
                date=entry.created_at.astimezone(TZ_MSK).strftime("%d.%m %H:%M"),
                reason=html.escape(entry.reason) if entry.reason else formatter._t["warns_no_reason"],
                by=_link(issuer) if issuer else "—",
            )
        )
    await _reply(message, "\n".join(lines))


@router.message(Command("unwarn"))
@inject
async def cmd_unwarn(
    message: Message,
    command: CommandObject,
    warn_service: FromDishka[WarnService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """/unwarn @user или ответом — снять последнее предупреждение."""
    if message.from_user is None or message.bot is None:
        return
    chat_id = message.chat.id
    if not await can_mute(message.bot, chat_id, message.from_user, config.admin.users):
        await reply_and_delete(message, formatter._t["mute_not_allowed"])
        return
    target, _, named = await _target(command.args, message, user_repo)
    if target is None:
        await reply_and_delete(message, formatter._t["error_user_not_found" if named else "unwarn_usage"])
        return

    left = await warn_service.unwarn(chat_id, target.id)
    if left is None:
        await _reply(message, formatter._t["warns_none"].format(target=_link(target)))
        return
    await _reply(message, formatter._t["unwarn_done"].format(target=_link(target), count=left, limit=config.warn.limit))
