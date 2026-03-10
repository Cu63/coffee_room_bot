from abc import ABC, abstractmethod
from datetime import datetime

from bot.domain.entities import MuteEntry


class IMuteRepository(ABC):
    @abstractmethod
    async def save(self, entry: MuteEntry) -> None: ...

    @abstractmethod
    async def get(self, user_id: int, chat_id: int) -> MuteEntry | None: ...

    @abstractmethod
    async def delete(self, user_id: int, chat_id: int) -> None: ...

    @abstractmethod
    async def get_expired(self, now: datetime) -> list[MuteEntry]:
        """Возвращает все муты с until_at <= now."""
        ...