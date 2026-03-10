from __future__ import annotations

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import LinkPreviewOptions, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import ScoreService
from bot.application.leaderboard_service import LeaderboardService
from bot.application.history_service import HistoryService
from bot.application.interfaces.user_repository import IUserRepository
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link

router = Router(name="commands")

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


@router.message(Command("score"))
@inject
async def cmd_score(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Показывает счёт вызвавшего или указанного пользователя."""
    chat_id = message.chat.id
    target_user = None

    if command.args:
        username = command.args.strip().lstrip("@")
        target_user = await user_repo.get_by_username(username)
        if target_user is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        display_name = user_link(target_user.username, target_user.full_name, target_user.id)
    else:
        if message.from_user is None:
            return
        display_name = user_link(
            message.from_user.username,
            message.from_user.full_name or "",
            message.from_user.id,
        )

    user_id = target_user.id if target_user else message.from_user.id  # type: ignore[union-attr]
    score = await score_service.get_score(user_id, chat_id)
    await message.reply(
        formatter.score_info(display_name, score.value),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("top"))
@inject
async def cmd_top(
    message: Message,
    command: CommandObject,
    leaderboard_service: FromDishka[LeaderboardService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Топ участников чата."""
    limit = 10
    if command.args:
        try:
            limit = max(1, min(50, int(command.args.strip())))
        except ValueError:
            limit = 10

    chat_id = message.chat.id
    top_scores = await leaderboard_service.get_top(chat_id, limit)

    rows: list[tuple[int, str, int]] = []
    for rank, score in enumerate(top_scores, start=1):
        user = await user_repo.get_by_id(score.user_id)
        if user:
            name = user_link(user.username, user.full_name, user.id)
        else:
            name = str(score.user_id)
        rows.append((rank, name, score.value))

    await message.reply(
        formatter.leaderboard(rows),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("history"))
@inject
async def cmd_history(
    message: Message,
    history_service: FromDishka[HistoryService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """История начислений за последние N дней."""
    chat_id = message.chat.id
    events = await history_service.get_history(chat_id)

    event_dicts: list[dict] = []
    for e in events:
        actor = await user_repo.get_by_id(e.actor_id)
        target = await user_repo.get_by_id(e.target_id)
        actor_name = user_link(actor.username, actor.full_name, actor.id) if actor else str(e.actor_id)
        target_name = user_link(target.username, target.full_name, target.id) if target else str(e.target_id)
        event_dicts.append({
            "date": e.created_at.strftime("%d.%m %H:%M") if e.created_at else "",
            "actor": actor_name,
            "target": target_name,
            "emoji": e.emoji,
            "delta": e.delta,
        })

    await message.reply(
        formatter.history(event_dicts, config.history.retention_days),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )