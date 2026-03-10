from abc import ABC, abstractmethod


class ISavedPermissionsRepository(ABC):
    @abstractmethod
    async def save(self, user_id: int, chat_id: int, permissions: dict) -> None: ...

    @abstractmethod
    async def get(self, user_id: int, chat_id: int) -> dict | None: ...

    @abstractmethod
    async def delete(self, user_id: int, chat_id: int) -> None: ...