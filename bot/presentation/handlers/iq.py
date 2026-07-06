"""Команды IQ: /iq, /topiq, /buyiq.

IQ — отдельная «валюта ума». По умолчанию у всех config.iq.default (89).
Растёт за победы в /ttt и /rword (лимит в сутки), падает за сообщения с
подстрокой «67». IQ можно докупить за кирчики (/buyiq)."""

from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.iq_service import IqService
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

router = Router(name="iq")


@router.message(Command("iq"))
@inject
async def cmd_iq(
    message: Message,
    command: CommandObject,
    iq_service: FromDishka[IqService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Показывает IQ вызвавшего или указанного пользователя (@username / реплай)."""
    if not config.iq.enabled:
        await reply_and_delete(message, "🧠 IQ-система отключена.")
        return

    chat_id = message.chat.id

    # Цель: реплай → @username → сам
    target_user = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = await user_repo.get_by_id(message.reply_to_message.from_user.id)
    elif command.args:
        target_user = await user_repo.get_by_username(command.args.strip().lstrip("@"))
        if target_user is None:
            await reply_and_delete(message, formatter._t.get("error_user_not_found", "Пользователь не найден."))
            return

    if target_user is not None:
        user_id = target_user.id
        display_name = user_link(target_user.username, target_user.full_name, target_user.id)
    else:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        display_name = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)

    iq = await iq_service.get_iq(user_id, chat_id)
    await reply_and_delete(
        message,
        f"🧠 IQ {display_name}: <b>{iq}</b>",
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("topiq"))
@inject
async def cmd_topiq(
    message: Message,
    command: CommandObject,
    iq_service: FromDishka[IqService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
) -> None:
    """Топ N участников чата по IQ (N по умолчанию — config.iq.top_default)."""
    if not config.iq.enabled:
        await reply_and_delete(message, "🧠 IQ-система отключена.")
        return

    n = config.iq.top_default
    if command.args:
        try:
            n = int(command.args.strip())
        except ValueError:
            n = config.iq.top_default
    limit = max(1, min(config.iq.top_max, n))

    entries = await iq_service.get_top(message.chat.id, limit)
    if not entries:
        await reply_and_delete(
            message,
            "🧠 <b>Топ по IQ</b>\n\n<i>Нет данных</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    users = await user_repo.get_by_ids([e.user_id for e in entries])
    lines = ["🧠 <b>Топ по IQ</b>"]
    for rank, entry in enumerate(entries, start=1):
        user = users.get(entry.user_id)
        name = user_link(user.username, user.full_name, user.id) if user else str(entry.user_id)
        lines.append(f"{rank}. {name} — <b>{entry.iq}</b> IQ")

    await reply_and_delete(
        message, "\n".join(lines), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


@router.message(Command("buyiq"))
@inject
async def cmd_buyiq(
    message: Message,
    command: CommandObject,
    iq_service: FromDishka[IqService],
    score_service: FromDishka[ScoreService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Покупка IQ за кирчики: пакет = buy_amount IQ за buy_cost. /buyiq [кол-во]."""
    if not config.iq.enabled:
        await reply_and_delete(message, "🧠 IQ-система отключена.")
        return
    if message.from_user is None:
        return

    cfg = config.iq
    p = formatter._p
    chat_id = message.chat.id
    user_id = message.from_user.id
    display = user_link(message.from_user.username, message.from_user.full_name or "", user_id)

    usage = (
        f"Использование: <code>/buyiq [кол-во пакетов]</code>\n"
        f"1 пакет = <b>{cfg.buy_amount} IQ</b> за {cfg.buy_cost} {p.pluralize(cfg.buy_cost)}."
    )

    packs = 1
    if command.args:
        try:
            packs = int(command.args.strip())
        except ValueError:
            await reply_and_delete(message, usage, parse_mode=ParseMode.HTML)
            return
    if packs < 1 or packs > cfg.max_buy_packs:
        await reply_and_delete(
            message,
            usage + f"\nДопустимо: 1–{cfg.max_buy_packs} пакетов за раз.",
            parse_mode=ParseMode.HTML,
        )
        return

    cost = cfg.buy_cost * packs
    gain = cfg.buy_amount * packs

    # Списываем кирчики (баллы уходят в «сток» экономики, боту не начисляем)
    result = await score_service.spend_score(
        actor_id=user_id,
        target_id=user_id,
        chat_id=chat_id,
        cost=cost,
        emoji=SPECIAL_EMOJI["iq"],
    )
    if not result.success:
        await reply_and_delete(
            message,
            f"Недостаточно баллов. Нужно: {cost} {p.pluralize(cost)}, "
            f"у тебя: {result.current_balance} {p.pluralize(result.current_balance)}.",
        )
        return

    new_iq = await iq_service.add_iq(user_id, chat_id, gain)
    await reply_and_delete(
        message,
        f"🧠 {display} купил <b>+{gain} IQ</b> за {cost} {p.pluralize(cost)}.\n"
        f"Теперь IQ: <b>{new_iq}</b> · баланс: {result.new_balance} {p.pluralize(result.new_balance)}",
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
