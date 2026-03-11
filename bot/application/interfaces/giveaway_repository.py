from abc import ABC, abstractmethod
from datetime import datetime

from bot.domain.giveaway_entities import Giveaway, GiveawayWinner


class IGiveawayRepository(ABC):
    @abstractmethod
    async def create(self, giveaway: Giveaway) -> Giveaway:
        """Создаёт розыгрыш, возвращает его с заполненным id."""
        ...

    @abstractmethod
    async def update_message_id(self, giveaway_id: int, message_id: int) -> None:
        """Сохраняет id сообщения-анонса после его отправки."""
        ...

    @abstractmethod
    async def get(self, giveaway_id: int) -> Giveaway | None: ...

    @abstractmethod
    async def get_active_in_chat(self, chat_id: int) -> list[Giveaway]: ...

    @abstractmethod
    async def finish(self, giveaway_id: int) -> None:
        """Переводит статус в finished."""
        ...

    @abstractmethod
    async def get_expired(self, now: datetime) -> list[Giveaway]:
        """Возвращает активные розыгрыши с ends_at <= now."""
        ...

    @abstractmethod
    async def add_participant(self, giveaway_id: int, user_id: int) -> bool:
        """Добавляет участника. Возвращает False если уже участвует."""
        ...

    @abstractmethod
    async def get_participants(self, giveaway_id: int) -> list[int]:
        """Список user_id всех участников."""
        ...

    @abstractmethod
    async def count_participants(self, giveaway_id: int) -> int: ...

    @abstractmethod
    async def save_winners(self, winners: list[GiveawayWinner]) -> None: ...

    @abstractmethod
    async def get_winners(self, giveaway_id: int) -> list[GiveawayWinner]: ...