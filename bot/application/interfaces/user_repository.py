from abc import ABC, abstractmethod

from bot.domain.entities import User


class IUserRepository(ABC):
    @abstractmethod
    async def upsert(self, user: User) -> None: ...

    @abstractmethod
    async def get_by_username(self, username: str) -> User | None: ...

    @abstractmethod
    async def get_by_id(self, user_id: int) -> User | None: ...

    @abstractmethod
    async def get_by_ids(self, user_ids: list[int]) -> dict[int, User]:
        """Загрузить несколько пользователей одним запросом. Ключ — user_id."""
        ...
