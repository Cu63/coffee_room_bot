from abc import ABC, abstractmethod
from datetime import datetime

from bot.domain.entities import ScoreEvent


class IEventRepository(ABC):
    """Реакции на сообщения: защита от повторной реакции и её снятие.

    История денег — в журнале операций (ILedgerRepository), не здесь.
    """

    @abstractmethod
    async def save(self, event: ScoreEvent) -> None: ...

    @abstractmethod
    async def exists(self, chat_id: int, actor_id: int, message_id: int, emoji: str) -> bool: ...

    @abstractmethod
    async def find_and_delete(self, chat_id: int, actor_id: int, message_id: int, emoji: str) -> ScoreEvent | None:
        """Находит событие, удаляет его и возвращает. None если не найдено."""
        ...

    @abstractmethod
    async def delete_before(self, cutoff: datetime) -> int:
        """Удаляет события старше cutoff. Возвращает количество удалённых."""
        ...
