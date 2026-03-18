from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ChatmodeEntry:
    chat_id: int
    mode: str                  # 'silence' | 'gif'
    activated_by: int          # user_id
    activated_at: datetime
    expires_at: datetime
    saved_perms: dict          # права чата до активации


class IChatmodeRepository(ABC):

    @abstractmethod
    async def get(self, chat_id: int) -> ChatmodeEntry | None:
        """Вернуть активный режим чата или None."""
        ...

    @abstractmethod
    async def save(self, entry: ChatmodeEntry) -> None:
        """Сохранить активный режим (upsert)."""
        ...

    @abstractmethod
    async def delete(self, chat_id: int) -> None:
        """Удалить режим (после истечения или ручной отмены)."""
        ...

    @abstractmethod
    async def get_expired(self, now: datetime) -> list[ChatmodeEntry]:
        """Вернуть все записи, у которых expires_at <= now."""
        ...
