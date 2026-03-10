from abc import ABC, abstractmethod

from bot.domain.entities import Score


class IScoreRepository(ABC):
    @abstractmethod
    async def get(self, user_id: int, chat_id: int) -> Score | None: ...

    @abstractmethod
    async def add_delta(self, user_id: int, chat_id: int, delta: int) -> int:
        """Атомарно изменяет счёт на delta. Возвращает новое значение."""
        ...

    @abstractmethod
    async def set_value(self, user_id: int, chat_id: int, value: int) -> int:
        """Устанавливает счёт в конкретное значение. Возвращает установленное значение."""
        ...

    @abstractmethod
    async def top(self, chat_id: int, limit: int) -> list[Score]: ...