from __future__ import annotations

from datetime import datetime

import asyncpg

from bot.application.interfaces.giveaway_repository import IGiveawayRepository
from bot.domain.giveaway_entities import Giveaway, GiveawayStatus, GiveawayWinner


class PostgresGiveawayRepository(IGiveawayRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(self, giveaway: Giveaway) -> Giveaway:
        row = await self._conn.fetchrow(
            """
            INSERT INTO giveaways (chat_id, created_by, prizes, ends_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id, created_at
            """,
            giveaway.chat_id,
            giveaway.created_by,
            giveaway.prizes,
            giveaway.ends_at,
        )
        giveaway.id = row["id"]
        giveaway.created_at = row["created_at"]
        return giveaway

    async def update_message_id(self, giveaway_id: int, message_id: int) -> None:
        await self._conn.execute(
            "UPDATE giveaways SET message_id = $1 WHERE id = $2",
            message_id,
            giveaway_id,
        )

    async def get(self, giveaway_id: int) -> Giveaway | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM giveaways WHERE id = $1",
            giveaway_id,
        )
        return self._row_to_giveaway(row) if row else None

    async def get_active_in_chat(self, chat_id: int) -> list[Giveaway]:
        rows = await self._conn.fetch(
            "SELECT * FROM giveaways WHERE chat_id = $1 AND status = 'active' ORDER BY created_at",
            chat_id,
        )
        return [self._row_to_giveaway(r) for r in rows]

    async def finish(self, giveaway_id: int) -> None:
        await self._conn.execute(
            "UPDATE giveaways SET status = 'finished' WHERE id = $1",
            giveaway_id,
        )

    async def get_expired(self, now: datetime) -> list[Giveaway]:
        rows = await self._conn.fetch(
            """
            SELECT * FROM giveaways
            WHERE status = 'active' AND ends_at IS NOT NULL AND ends_at <= $1
            """,
            now,
        )
        return [self._row_to_giveaway(r) for r in rows]

    async def add_participant(self, giveaway_id: int, user_id: int) -> bool:
        try:
            await self._conn.execute(
                """
                INSERT INTO giveaway_participants (giveaway_id, user_id)
                VALUES ($1, $2)
                """,
                giveaway_id,
                user_id,
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

    async def get_participants(self, giveaway_id: int) -> list[int]:
        rows = await self._conn.fetch(
            "SELECT user_id FROM giveaway_participants WHERE giveaway_id = $1",
            giveaway_id,
        )
        return [r["user_id"] for r in rows]

    async def count_participants(self, giveaway_id: int) -> int:
        row = await self._conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM giveaway_participants WHERE giveaway_id = $1",
            giveaway_id,
        )
        return row["cnt"] if row else 0

    async def save_winners(self, winners: list[GiveawayWinner]) -> None:
        await self._conn.executemany(
            """
            INSERT INTO giveaway_winners (giveaway_id, user_id, prize, position)
            VALUES ($1, $2, $3, $4)
            """,
            [(w.giveaway_id, w.user_id, w.prize, w.position) for w in winners],
        )

    async def get_winners(self, giveaway_id: int) -> list[GiveawayWinner]:
        rows = await self._conn.fetch(
            "SELECT * FROM giveaway_winners WHERE giveaway_id = $1 ORDER BY position",
            giveaway_id,
        )
        return [
            GiveawayWinner(
                giveaway_id=r["giveaway_id"],
                user_id=r["user_id"],
                prize=r["prize"],
                position=r["position"],
            )
            for r in rows
        ]

    @staticmethod
    def _row_to_giveaway(row: asyncpg.Record) -> Giveaway:
        return Giveaway(
            id=row["id"],
            chat_id=row["chat_id"],
            created_by=row["created_by"],
            prizes=list(row["prizes"]),
            status=GiveawayStatus(row["status"]),
            message_id=row["message_id"],
            ends_at=row["ends_at"],
            created_at=row["created_at"],
        )