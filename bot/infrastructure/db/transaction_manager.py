import asyncpg

from bot.application.interfaces.transaction_manager import ITransactionManager


class PostgresTransactionManager(ITransactionManager):
    """Управляет жизненным циклом транзакции.

    Коммит/роллбэк происходят ТОЛЬКО здесь.
    Ни репозитории, ни юзкейсы не вызывают commit/rollback.
    Жизненный цикл управляется через DI-контейнер (dishka).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._conn: asyncpg.Connection | None = None
        self._tx: asyncpg.connection.transaction.Transaction | None = None

    def get_connection(self) -> asyncpg.Connection:
        if self._conn is None:
            raise RuntimeError("Transaction not started — call begin() first")
        return self._conn

    async def begin(self) -> None:
        self._conn = await self._pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()

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
