from datetime import datetime

import asyncpg

from bot.domain.entities import Direction, ScoreEvent
from bot.application.interfaces.event_repository import IEventRepository


class PostgresEventRepository(IEventRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def save(self, event: ScoreEvent) -> None:
        await self._conn.execute(
            """
            INSERT INTO score_events (chat_id, actor_id, target_id, message_id, emoji, delta, direction)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            event.chat_id,
            event.actor_id,
            event.target_id,
            event.message_id,
            event.emoji,
            event.delta,
            event.direction.value,
        )

    async def exists(self, actor_id: int, message_id: int, emoji: str) -> bool:
        row = await self._conn.fetchval(
            """
            SELECT 1 FROM score_events
            WHERE actor_id = $1 AND message_id = $2 AND emoji = $3
            """,
            actor_id,
            message_id,
            emoji,
        )
        return row is not None

    async def find_and_delete(self, actor_id: int, message_id: int, emoji: str) -> ScoreEvent | None:
        row = await self._conn.fetchrow(
            """
            DELETE FROM score_events
            WHERE actor_id = $1 AND message_id = $2 AND emoji = $3
            RETURNING id, chat_id, actor_id, target_id, message_id, emoji, delta, direction, created_at
            """,
            actor_id,
            message_id,
            emoji,
        )
        if row is None:
            return None
        return self._to_entity(row)

    async def get_history(self, chat_id: int, since: datetime) -> list[ScoreEvent]:
        rows = await self._conn.fetch(
            """
            SELECT id, chat_id, actor_id, target_id, message_id, emoji, delta, direction, created_at
            FROM score_events
            WHERE chat_id = $1 AND created_at >= $2
            ORDER BY created_at DESC
            """,
            chat_id,
            since,
        )
        return [self._to_entity(r) for r in rows]

    async def delete_before(self, cutoff: datetime) -> int:
        result = await self._conn.execute(
            "DELETE FROM score_events WHERE created_at < $1",
            cutoff,
        )
        # asyncpg returns 'DELETE N'
        return int(result.split()[-1])

    @staticmethod
    def _to_entity(row: asyncpg.Record) -> ScoreEvent:
        return ScoreEvent(
            id=row["id"],
            chat_id=row["chat_id"],
            actor_id=row["actor_id"],
            target_id=row["target_id"],
            message_id=row["message_id"],
            emoji=row["emoji"],
            delta=row["delta"],
            direction=Direction(row["direction"]),
            created_at=row["created_at"],
        )
