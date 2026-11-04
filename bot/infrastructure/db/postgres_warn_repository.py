from datetime import datetime

import asyncpg

from bot.application.interfaces.warn_repository import IWarnRepository
from bot.domain.entities import WarnEntry


class PostgresWarnRepository(IWarnRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def lock(self, chat_id: int, user_id: int) -> None:
        await self._conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(format('warn:%s:%s', $1::bigint, $2::bigint), 0))",
            chat_id,
            user_id,
        )

    async def add(self, chat_id: int, user_id: int, issued_by: int, reason: str) -> None:
        await self._conn.execute(
            "INSERT INTO warnings (chat_id, user_id, issued_by, reason) VALUES ($1, $2, $3, $4)",
            chat_id,
            user_id,
            issued_by,
            reason,
        )

    async def active(self, chat_id: int, user_id: int, since: datetime) -> list[WarnEntry]:
        rows = await self._conn.fetch(
            """
            SELECT id, chat_id, user_id, issued_by, reason, created_at FROM warnings
            WHERE chat_id = $1 AND user_id = $2 AND created_at > $3
            ORDER BY created_at DESC, id DESC
            """,
            chat_id,
            user_id,
            since,
        )
        return [WarnEntry(**dict(r)) for r in rows]

    async def delete_latest(self, chat_id: int, user_id: int, since: datetime) -> bool:
        deleted = await self._conn.fetchval(
            """
            DELETE FROM warnings WHERE id = (
                SELECT id FROM warnings
                WHERE chat_id = $1 AND user_id = $2 AND created_at > $3
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            )
            RETURNING id
            """,
            chat_id,
            user_id,
            since,
        )
        return deleted is not None

    async def clear(self, chat_id: int, user_id: int) -> None:
        await self._conn.execute("DELETE FROM warnings WHERE chat_id = $1 AND user_id = $2", chat_id, user_id)
