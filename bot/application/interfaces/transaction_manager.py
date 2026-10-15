from abc import ABC, abstractmethod
from typing import Any


class ITransactionManager(ABC):
    """Транзакция запроса. Коммит/роллбэк управляются здесь, а не в репозиториях или юзкейсах.

    Транзакция открывается лениво — первым запросом к БД. ``checkpoint()``
    фиксирует сделанное и возвращает соединение в пул; следующий запрос откроет
    новую транзакцию. Его вызывают перед медленным внешним вызовом (LLM, поиск):
    иначе соединение из пула занято всё время ожидания.
    """

    @abstractmethod
    def get_connection(self) -> Any:
        """Соединение для репозиториев: годится на всё время запроса, переживает checkpoint()."""
        ...

    @abstractmethod
    async def checkpoint(self) -> None:
        """Закоммитить сделанное и отпустить соединение. Откат после этого затронет только новое."""
        ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...
