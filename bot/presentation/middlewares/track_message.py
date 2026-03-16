from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, ReactionTypeEmoji, TelegramObject
from dishka import AsyncContainer

from bot.application.interfaces.message_repository import IMessageRepository, MessageInfo
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.reaction_registry import ReactionRegistry
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


class TrackMessageMiddleware(BaseMiddleware):
    """Записывает автора и время каждого входящего сообщения.
    Опционально ставит случайную реакцию с заданной вероятностью.

    Работает как outer-middleware на Message — вызывается ДО хэндлеров,
    поэтому и команды, и обычные сообщения трекаются.

    bot_me передаётся при инициализации из main() — один раз при старте,
    чтобы не делать лишний запрос к Telegram API на каждый авто-реакт.
    """

    def __init__(self, bot_me) -> None:
        super().__init__()
        self._bot_me = bot_me

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            container: AsyncContainer = data["dishka_container"]

            user_repo = await container.get(IUserRepository)
            message_repo = await container.get(IMessageRepository)

            await user_repo.upsert(
                User(
                    id=event.from_user.id,
                    username=event.from_user.username,
                    full_name=event.from_user.full_name or "",
                )
            )
            await message_repo.save(
                MessageInfo(
                    message_id=event.message_id,
                    chat_id=event.chat.id,
                    user_id=event.from_user.id,
                    sent_at=event.date or datetime.now(TZ_MSK),
                )
            )

            await self._maybe_react(event, container)
            await self._maybe_burst(event, container)

        return await handler(event, data)

    async def _maybe_react(self, message: Message, container: AsyncContainer) -> None:
        config = await container.get(AppConfig)
        cfg = config.auto_react

        if not cfg.enabled:
            return
        if message.bot is None or message.from_user is None:
            return
        # Не реагируем на сообщения самого бота
        if message.from_user.id == self._bot_me.id:
            return
        # Бросаем кубик
        if random.random() >= cfg.probability:
            return

        registry = await container.get(ReactionRegistry)
        reactions = [(emoji, r) for emoji, r in registry._reactions.items() if not cfg.positive_only or r.weight > 0]
        if not reactions:
            return

        emoji, _ = random.choice(reactions)

        try:
            await message.bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(type="emoji", emoji=emoji)],
            )
        except Exception as e:
            logger.debug("auto_react: failed to set reaction: %s", e)
            return

        # Засчитываем реакцию вручную — бот не получает события о своих реакциях.
        # Сначала upsert бота в users (иначе FK на score_events упадёт),
        # затем применяем реакцию без лимитов (бот не ограничен).
        user_repo = await container.get(IUserRepository)
        # Используем кешированный bot_me вместо bot.get_me() (экономим один запрос к API)
        await user_repo.upsert(
            User(
                id=self._bot_me.id,
                username=self._bot_me.username,
                full_name=self._bot_me.full_name,
            )
        )

        score_service = await container.get(ScoreService)
        result = await score_service.apply_reaction_no_limits(
            actor_id=self._bot_me.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            emoji=emoji,
        )
        logger.debug(
            "auto_react: %s on msg %d — applied=%s",
            emoji,
            message.message_id,
            result.applied,
        )

    async def _maybe_burst(self, message: Message, container: AsyncContainer) -> None:
        config = await container.get(AppConfig)
        cfg = config.burst

        if not cfg.enabled:
            return
        if message.from_user is None:
            return
        # Боты не участвуют
        if message.from_user.is_bot:
            return
        # Команды не засчитываются
        if message.text and message.text.startswith("/"):
            return
        # Форварды не засчитываются
        if message.forward_origin is not None:
            return

        # Текст сообщения (text или caption)
        text = message.text or message.caption or ""
        if len(text) < cfg.min_length:
            return

        user_id = message.from_user.id
        chat_id = message.chat.id

        store = await container.get(RedisStore)

        # Кулдаун активен — пропускаем
        if await store.burst_cooldown_active(user_id, chat_id):
            return

        window_seconds = cfg.window_minutes * 60
        count = await store.burst_add_message(user_id, chat_id, window_seconds)

        if count >= cfg.messages_required:
            score_service = await container.get(ScoreService)
            cooldown_seconds = cfg.cooldown_hours * 3600
            await store.burst_set_cooldown(user_id, chat_id, cooldown_seconds)
            new_value = await score_service.award_burst(user_id, chat_id, cfg.reward)
            logger.debug(
                "burst: user %d in chat %d awarded %d, new score %d",
                user_id, chat_id, cfg.reward, new_value,
            )
