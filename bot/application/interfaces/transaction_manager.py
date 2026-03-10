from abc import ABC, abstractmethod
from typing import Any


class ITransactionManager(ABC):
    """Менеджер транзакций. Коммит/роллбэк управляются здесь, а не в репозиториях или юзкейсах."""

    @abstractmethod
    def get_connection(self) -> Any:
        """Возвращает текущее транзакционное соединение."""
        ...

    @abstractmethod
    async def begin(self) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...
