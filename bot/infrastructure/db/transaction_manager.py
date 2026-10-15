import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from bot.application.interfaces.transaction_manager import ITransactionManager


class PostgresTransactionManager(ITransactionManager):
    """Управляет жизненным циклом транзакции запроса.

    Коммит/роллбэк происходят ТОЛЬКО здесь.
    Ни репозитории, ни юзкейсы не вызывают commit/rollback.
    Жизненный цикл управляется через DI-контейнер (dishka); внутри запроса
    транзакцию можно зафиксировать раньше через checkpoint().
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._conn: asyncpg.Connection | None = None
        self._tx: asyncpg.connection.transaction.Transaction | None = None
        self._opening = asyncio.Lock()
        self._lazy = LazyConnection(self)

    def get_connection(self) -> "LazyConnection":
        return self._lazy

    @property
    def in_transaction(self) -> bool:
        """Держит ли запрос сейчас соединение из пула."""
        return self._conn is not None

    async def acquire(self) -> asyncpg.Connection:
        """Соединение текущей транзакции; первая операция открывает её."""
        if self._conn is None:
            async with self._opening:
                if self._conn is None:
                    conn = await self._pool.acquire()
                    try:
                        tx = conn.transaction()
                        await tx.start()
                    except BaseException:
                        await self._pool.release(conn)
                        raise
                    self._conn, self._tx = conn, tx
        return self._conn

    async def checkpoint(self) -> None:
        await self.commit()

    @asynccontextmanager
    async def savepoint(self) -> AsyncIterator[None]:
        conn = await self.acquire()
        async with conn.transaction():  # внутри открытой транзакции asyncpg делает SAVEPOINT
            yield

    async def commit(self) -> None:
        try:
            if self._tx is not None:
                await self._tx.commit()
        finally:
            await self._release()

    async def rollback(self) -> None:
        try:
            if self._tx is not None:
                await self._tx.rollback()
        finally:
            await self._release()

    async def _release(self) -> None:
        conn, self._conn = self._conn, None
        self._tx = None
        if conn is not None:
            await self._pool.release(conn)


class LazyConnection:
    """То, что видят репозитории вместо asyncpg.Connection.

    Каждый вызов идёт в соединение текущей транзакции и открывает её при
    необходимости, поэтому репозиторий, созданный в начале запроса, продолжает
    работать после checkpoint().
    """

    def __init__(self, tm: PostgresTransactionManager) -> None:
        self._tm = tm

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> str:
        return await (await self._tm.acquire()).execute(query, *args, **kwargs)

    async def executemany(self, command: str, args: Any, **kwargs: Any) -> None:
        return await (await self._tm.acquire()).executemany(command, args, **kwargs)

    async def fetch(self, query: str, *args: Any, **kwargs: Any) -> list[asyncpg.Record]:
        return await (await self._tm.acquire()).fetch(query, *args, **kwargs)

    async def fetchrow(self, query: str, *args: Any, **kwargs: Any) -> asyncpg.Record | None:
        return await (await self._tm.acquire()).fetchrow(query, *args, **kwargs)

    async def fetchval(self, query: str, *args: Any, **kwargs: Any) -> Any:
        return await (await self._tm.acquire()).fetchval(query, *args, **kwargs)
