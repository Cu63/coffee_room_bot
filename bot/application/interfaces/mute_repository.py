from abc import ABC, abstractmethod
from datetime import datetime

from bot.domain.entities import ActiveMute, MuteEntry, User


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

    @abstractmethod
    async def set_admin_title(self, user_id: int, chat_id: int, title: str | None) -> None:
        """Титул в снимке прав замученного админа (None — без титула); не админа не трогает."""
        ...

    @abstractmethod
    async def active_in_chat(self, chat_id: int) -> list[ActiveMute]:
        """Действующие муты чата — сначала те, что кончатся раньше."""
        ...

    @abstractmethod
    async def released_users(self, chat_id: int) -> list[User]:
        """Все, кого бот когда-либо мутил в чате и кто сейчас не в муте."""
        ...

    @abstractmethod
    async def log_mute(self, user_id: int, muted_by: int, chat_id: int) -> None:
        """Записывает факт мута в историю для статистики."""
        ...
