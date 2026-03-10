from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import MessageReactionUpdated, ReactionTypeEmoji
from dishka.integrations.aiogram import FromDishka, inject

from bot.domain.entities import User
from bot.application.score_service import ScoreService
from bot.application.interfaces.user_repository import IUserRepository

logger = logging.getLogger(__name__)
router = Router(name="reactions")


@router.message_reaction()
@inject
async def on_reaction_changed(
    event: MessageReactionUpdated,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
) -> None:
    """Обрабатывает добавление и снятие реакций."""
    if event.user is None:
        # Анонимные реакции не обрабатываются
        return

    actor = event.user
    chat_id = event.chat.id
    message_id = event.message_id

    # Сохраняем/обновляем пользователя
    await user_repo.upsert(
        User(
            id=actor.id,
            username=actor.username,
            full_name=actor.full_name or "",
        )
    )

    old_emojis = _extract_emojis(event.old_reaction)
    new_emojis = _extract_emojis(event.new_reaction)

    added = new_emojis - old_emojis
    removed = old_emojis - new_emojis

    for emoji in added:
        result = await score_service.apply_reaction(
            actor_id=actor.id,
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
        )
        if result.applied:
            logger.debug(
                "Reaction %s by %d on msg %d: delta=%+d, new=%d",
                emoji, actor.id, message_id, result.delta, result.new_value,
            )

    for emoji in removed:
        result = await score_service.remove_reaction(
            actor_id=actor.id,
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
        )
        if result.applied:
            logger.debug(
                "Reaction %s removed by %d on msg %d: delta=%+d, new=%d",
                emoji, actor.id, message_id, result.delta, result.new_value,
            )


def _extract_emojis(reactions: list | None) -> set[str]:
    if not reactions:
        return set()
    result: set[str] = set()
    for r in reactions:
        if isinstance(r, ReactionTypeEmoji):
            result.add(r.emoji)
    return result
