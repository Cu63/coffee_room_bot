from abc import ABC, abstractmethod
from datetime import datetime

from bot.domain.entities import WarnEntry


class IWarnRepository(ABC):
    @abstractmethod
    async def lock(self, chat_id: int, user_id: int) -> None:
        """Блокировка до конца транзакции: предупреждения одному участнику выдаются по очереди."""
        ...

    @abstractmethod
    async def add(self, chat_id: int, user_id: int, issued_by: int, reason: str) -> None: ...

    @abstractmethod
    async def active(self, chat_id: int, user_id: int, since: datetime) -> list[WarnEntry]:
        """Предупреждения, выданные после since, — сначала новые."""
        ...

    @abstractmethod
    async def delete_latest(self, chat_id: int, user_id: int, since: datetime) -> bool:
        """Удалить последнее предупреждение, выданное после since. False — таких нет."""
        ...

    @abstractmethod
    async def clear(self, chat_id: int, user_id: int) -> None: ...
