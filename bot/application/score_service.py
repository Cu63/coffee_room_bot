from dataclasses import dataclass
from datetime import date, datetime, timedelta
from bot.domain.tz import TZ_MSK
import time

from bot.domain.entities import (
    ApplyResult,
    Direction,
    IgnoreReason,
    Score,
    ScoreEvent,
)
from bot.domain.reaction_registry import ReactionRegistry
from bot.domain.emoji_utils import normalize_emoji

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.event_repository import IEventRepository
from bot.application.interfaces.daily_limits_repository import IDailyLimitsRepository
from bot.application.interfaces.message_repository import IMessageRepository

# Эмодзи-маркеры для специальных действий в истории
SPECIAL_EMOJI = {
    "set": "⚙️",
    "reset": "🔄",
    "add": "➕",
    "sub": "➖",
    "mute": "🔇",
    "tag": "🏷",
}


@dataclass(slots=True)
class SpendResult:
    success: bool
    cost: int = 0
    new_balance: int = 0
    current_balance: int = 0  # для сообщения об ошибке «у вас N баллов»


class ScoreService:
    def __init__(
        self,
        score_repo: IScoreRepository,
        event_repo: IEventRepository,
        limits_repo: IDailyLimitsRepository,
        message_repo: IMessageRepository,
        reaction_registry: ReactionRegistry,
        self_reaction_allowed: bool,
        daily_reactions_given: int,
        daily_score_received: int,
        max_message_age_hours: int,
    ) -> None:
        self._score_repo = score_repo
        self._event_repo = event_repo
        self._limits_repo = limits_repo
        self._message_repo = message_repo
        self._registry = reaction_registry
        self._self_reaction_allowed = self_reaction_allowed
        self._daily_reactions_given = daily_reactions_given
        self._daily_score_received = daily_score_received
        self._max_message_age_hours = max_message_age_hours

    async def apply_reaction(
        self,
        actor_id: int,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> ApplyResult:
        emoji = normalize_emoji(emoji)
        # 1. Реакция в реестре?
        reaction = self._registry.get(emoji)
        if reaction is None:
            return ApplyResult(applied=False, reason=IgnoreReason.UNKNOWN_REACTION)
        if reaction.weight == 0:
            return ApplyResult(applied=False, reason=IgnoreReason.ZERO_WEIGHT)

        # 2. Кто автор сообщения?
        msg = await self._message_repo.get(chat_id, message_id)
        if msg is None:
            return ApplyResult(applied=False, reason=IgnoreReason.UNKNOWN_MESSAGE)

        target_id = msg.user_id

        # 3. Самореакция
        if not self._self_reaction_allowed and actor_id == target_id:
            return ApplyResult(applied=False, reason=IgnoreReason.SELF_REACTION)

        # 4. Негативная реакция от пользователя с отрицательным счётом
        if reaction.weight < 0:
            actor_score = await self._score_repo.get(actor_id, chat_id)
            if actor_score is not None and actor_score.value < 0:
                return ApplyResult(applied=False, reason=IgnoreReason.NEGATIVE_SCORE_ACTOR)

        # 5. Возраст сообщения
        now = datetime.now(TZ_MSK)
        age = now - msg.sent_at
        if age > timedelta(hours=self._max_message_age_hours):
            return ApplyResult(applied=False, reason=IgnoreReason.MESSAGE_TOO_OLD)

        # 6. Уникальность
        if await self._event_repo.exists(actor_id, message_id, emoji):
            return ApplyResult(applied=False, reason=IgnoreReason.DUPLICATE)

        # 7. Дневной лимит реакций актора
        today = date.today()
        actor_limits = await self._limits_repo.get(actor_id, chat_id, today)
        if actor_limits.reactions_given >= self._daily_reactions_given:
            return ApplyResult(applied=False, reason=IgnoreReason.DAILY_REACTIONS_LIMIT)

        # 8. Дневной лимит очков получателя
        target_limits = await self._limits_repo.get(target_id, chat_id, today)
        if target_limits.score_received >= self._daily_score_received:
            return ApplyResult(applied=False, reason=IgnoreReason.DAILY_SCORE_LIMIT)

        # 9. Применить
        delta = reaction.weight
        new_value = await self._score_repo.add_delta(target_id, chat_id, delta)

        await self._event_repo.save(
            ScoreEvent(
                chat_id=chat_id,
                actor_id=actor_id,
                target_id=target_id,
                message_id=message_id,
                emoji=emoji,
                delta=delta,
                direction=Direction.ADD,
            )
        )

        await self._limits_repo.increment_given(actor_id, chat_id, today, 1)
        await self._limits_repo.increment_received(target_id, chat_id, today, abs(delta))

        return ApplyResult(applied=True, delta=delta, new_value=new_value)

    async def remove_reaction(
        self,
        actor_id: int,
        chat_id: int,
        message_id: int,
        emoji: str,
    ) -> ApplyResult:
        emoji = normalize_emoji(emoji)
        # Найти и удалить оригинальное событие
        event = await self._event_repo.find_and_delete(actor_id, message_id, emoji)
        if event is None:
            return ApplyResult(applied=False, reason=IgnoreReason.EVENT_NOT_FOUND)

        # Обратное изменение счёта
        reverse_delta = -event.delta
        new_value = await self._score_repo.add_delta(event.target_id, chat_id, reverse_delta)

        # Откат лимитов
        today = date.today()
        await self._limits_repo.increment_given(actor_id, chat_id, today, -1)
        await self._limits_repo.increment_received(event.target_id, chat_id, today, -abs(event.delta))

        return ApplyResult(applied=True, delta=reverse_delta, new_value=new_value)

    async def get_score(self, user_id: int, chat_id: int) -> Score:
        score = await self._score_repo.get(user_id, chat_id)
        if score is None:
            return Score(user_id=user_id, chat_id=chat_id, value=0)
        return score

    async def set_score(
        self, user_id: int, chat_id: int, value: int, *, admin_id: int,
    ) -> int:
        """Админская установка счёта в конкретное значение."""
        old = await self._score_repo.get(user_id, chat_id)
        old_value = old.value if old else 0
        new_value = await self._score_repo.set_value(user_id, chat_id, value)
        delta = new_value - old_value
        if delta != 0:
            await self._save_special_event(
                admin_id, user_id, chat_id, delta,
                SPECIAL_EMOJI["reset"] if value == 0 else SPECIAL_EMOJI["set"],
            )
        return new_value

    async def add_score(
        self, user_id: int, chat_id: int, delta: int, *, admin_id: int,
    ) -> int:
        """Админское изменение счёта на delta."""
        new_value = await self._score_repo.add_delta(user_id, chat_id, delta)
        emoji = SPECIAL_EMOJI["add"] if delta >= 0 else SPECIAL_EMOJI["sub"]
        await self._save_special_event(admin_id, user_id, chat_id, delta, emoji)
        return new_value

    async def spend_score(
        self,
        actor_id: int,
        target_id: int,
        chat_id: int,
        cost: int,
        emoji: str = "",
    ) -> SpendResult:
        """Списать баллы у actor за действие над target.

        Возвращает SpendResult. В долг не даётся — если баланс < cost, отказ.
        """
        score = await self._score_repo.get(actor_id, chat_id)
        balance = score.value if score else 0

        if balance < cost:
            return SpendResult(success=False, cost=cost, current_balance=balance)

        new_balance = await self._score_repo.add_delta(actor_id, chat_id, -cost)

        await self._save_special_event(
            actor_id, target_id, chat_id, -cost, emoji or SPECIAL_EMOJI["mute"],
        )

        return SpendResult(success=True, cost=cost, new_balance=new_balance)

    async def _save_special_event(
        self,
        actor_id: int,
        target_id: int,
        chat_id: int,
        delta: int,
        emoji: str,
    ) -> None:
        # Отрицательный message_id на базе времени — уникален и отличается от реальных сообщений
        pseudo_message_id = -int(time.time() * 1000)
        await self._event_repo.save(
            ScoreEvent(
                chat_id=chat_id,
                actor_id=actor_id,
                target_id=target_id,
                message_id=pseudo_message_id,
                emoji=emoji,
                delta=delta,
                direction=Direction.ADD,
            )
        )