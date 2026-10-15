from datetime import datetime

import asyncpg

from bot.application.interfaces.ledger_repository import ILedgerRepository, LedgerMismatch
from bot.domain.ledger import LedgerEntry


class PostgresLedgerRepository(ILedgerRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def mismatches(self, limit: int = 100) -> list[LedgerMismatch]:
        rows = await self._conn.fetch(
            """
            SELECT COALESCE(s.chat_id, l.chat_id) AS chat_id,
                   COALESCE(s.user_id, l.user_id) AS user_id,
                   COALESCE(s.value, 0) AS balance,
                   COALESCE(l.total, 0) AS ledger_total
            FROM scores s
            FULL JOIN (
                SELECT chat_id, user_id, SUM(delta) AS total FROM ledger GROUP BY chat_id, user_id
            ) l ON l.chat_id = s.chat_id AND l.user_id = s.user_id
            WHERE COALESCE(s.value, 0) <> COALESCE(l.total, 0)
            ORDER BY 1, 2
            LIMIT $1
            """,
            limit,
        )
        return [
            LedgerMismatch(
                chat_id=r["chat_id"], user_id=r["user_id"], balance=r["balance"], ledger_total=r["ledger_total"]
            )
            for r in rows
        ]

    async def history(
        self, chat_id: int, since: datetime, user_id: int | None = None, limit: int = 500
    ) -> list[LedgerEntry]:
        rows = await self._conn.fetch(
            """
            SELECT l.chat_id, l.user_id, l.delta, l.kind, l.ref, l.counterparty_id, l.created_at
            FROM ledger l
            JOIN users u ON u.id = l.user_id
            WHERE l.chat_id = $1
              AND l.created_at >= $2
              AND l.kind <> 'opening'
              AND NOT u.is_bot
              AND ($3::bigint IS NULL OR l.user_id = $3 OR l.counterparty_id = $3)
            ORDER BY l.id DESC
            LIMIT $4
            """,
            chat_id,
            since,
            user_id,
            limit,
        )
        return [
            LedgerEntry(
                chat_id=r["chat_id"],
                user_id=r["user_id"],
                delta=r["delta"],
                kind=r["kind"],
                ref=r["ref"],
                counterparty_id=r["counterparty_id"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def count_unknown_since(self, since: datetime) -> int:
        return await self._conn.fetchval(
            "SELECT count(*) FROM ledger WHERE kind = 'unknown' AND created_at >= $1",
            since,
        )
