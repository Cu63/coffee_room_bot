from abc import ABC, abstractmethod
from datetime import datetime

from bot.domain.entities import ScoreEvent


class IEventRepository(ABC):
    @abstractmethod
    async def save(self, event: ScoreEvent) -> None: ...

    @abstractmethod
    async def exists(self, actor_id: int, message_id: int, emoji: str) -> bool: ...

    @abstractmethod
    async def find_and_delete(self, actor_id: int, message_id: int, emoji: str) -> ScoreEvent | None:
        """Находит событие, удаляет его и возвращает. None если не найдено."""
        ...

    @abstractmethod
    async def get_history(self, chat_id: int, since: datetime) -> list[ScoreEvent]: ...

    @abstractmethod
    async def delete_before(self, cutoff: datetime) -> int:
        """Удаляет события старше cutoff. Возвращает количество удалённых."""
        ...
