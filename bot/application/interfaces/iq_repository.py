from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class UserIq:
    user_id: int
    chat_id: int
    iq: int


class IIqRepository(ABC):
    @abstractmethod
    async def get_iq(self, user_id: int, chat_id: int) -> int | None:
        """Возвращает текущий IQ пользователя или None, если записи ещё нет."""
        ...

    @abstractmethod
    async def add_iq(self, user_id: int, chat_id: int, delta: int, default: int) -> int:
        """Атомарно изменяет IQ на delta. Если записи нет — стартует от `default`.
        Возвращает новое значение IQ."""
        ...

    @abstractmethod
    async def set_iq(self, user_id: int, chat_id: int, value: int) -> None:
        """Устанавливает IQ в конкретное значение."""
        ...

    @abstractmethod
    async def top(self, chat_id: int, limit: int) -> list[UserIq]:
        """Топ пользователей чата по IQ (по убыванию)."""
        ...
