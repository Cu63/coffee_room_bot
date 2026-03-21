from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class UserXp:
    user_id: int
    chat_id: int
    xp: int


class IXpRepository(ABC):
    @abstractmethod
    async def add_xp(self, user_id: int, chat_id: int, amount: int) -> int:
        """Атомарно добавляет amount XP. Возвращает новое суммарное XP."""
        ...

    @abstractmethod
    async def get_xp(self, user_id: int, chat_id: int) -> int:
        """Возвращает текущее XP пользователя (0 если нет записи)."""
        ...

    @abstractmethod
    async def top(self, chat_id: int, limit: int) -> list[UserXp]:
        """Топ пользователей чата по XP."""
        ...
