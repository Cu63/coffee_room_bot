from datetime import datetime

import asyncpg

from bot.application.interfaces.event_repository import IEventRepository
from bot.domain.entities import Direction, ScoreEvent


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

    async def exists(self, chat_id: int, actor_id: int, message_id: int, emoji: str) -> bool:
        row = await self._conn.fetchval(
            """
            SELECT 1 FROM score_events
            WHERE chat_id = $1 AND actor_id = $2 AND message_id = $3 AND emoji = $4
            """,
            chat_id,
            actor_id,
            message_id,
            emoji,
        )
        return row is not None

    async def find_and_delete(self, chat_id: int, actor_id: int, message_id: int, emoji: str) -> ScoreEvent | None:
        row = await self._conn.fetchrow(
            """
            DELETE FROM score_events
            WHERE chat_id = $1 AND actor_id = $2 AND message_id = $3 AND emoji = $4
            RETURNING id, chat_id, actor_id, target_id, message_id, emoji, delta, direction, created_at
            """,
            chat_id,
            actor_id,
            message_id,
            emoji,
        )
        if row is None:
            return None
        return self._to_entity(row)

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
