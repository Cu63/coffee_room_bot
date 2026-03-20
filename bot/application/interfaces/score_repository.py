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

    @abstractmethod
    async def bottom(self, chat_id: int, limit: int) -> list[Score]:
        """Пользователи с наименьшим счётом (антирейтинг), ascending."""
        ...

    @abstractmethod
    async def get_rank(self, user_id: int, chat_id: int) -> int | None:
        """Место пользователя в рейтинге чата (1 = первое). None если нет счёта."""
        ...