import asyncpg

from bot.application.interfaces.score_repository import IScoreRepository
from bot.domain.entities import Score


class PostgresScoreRepository(IScoreRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get(self, user_id: int, chat_id: int) -> Score | None:
        row = await self._conn.fetchrow(
            "SELECT user_id, chat_id, value FROM scores WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )
        if row is None:
            return None
        return Score(user_id=row["user_id"], chat_id=row["chat_id"], value=row["value"])

    async def add_delta(self, user_id: int, chat_id: int, delta: int) -> int:
        row = await self._conn.fetchrow(
            """
            INSERT INTO scores (user_id, chat_id, value)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET value = scores.value + EXCLUDED.value
            RETURNING value
            """,
            user_id,
            chat_id,
            delta,
        )
        return row["value"]  # type: ignore[index]

    async def set_value(self, user_id: int, chat_id: int, value: int) -> int:
        row = await self._conn.fetchrow(
            """
            INSERT INTO scores (user_id, chat_id, value)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET value = EXCLUDED.value
            RETURNING value
            """,
            user_id,
            chat_id,
            value,
        )
        return row["value"]  # type: ignore[index]

    async def top(self, chat_id: int, limit: int) -> list[Score]:
        rows = await self._conn.fetch(
            """
            SELECT user_id, chat_id, value
            FROM scores
            WHERE chat_id = $1 AND value != 0
            ORDER BY value DESC
            LIMIT $2
            """,
            chat_id,
            limit,
        )
        return [Score(user_id=r["user_id"], chat_id=r["chat_id"], value=r["value"]) for r in rows]

    async def bottom(self, chat_id: int, limit: int) -> list[Score]:
        rows = await self._conn.fetch(
            """
            SELECT user_id, chat_id, value
            FROM scores
            WHERE chat_id = $1 AND value != 0
            ORDER BY value ASC
            LIMIT $2
            """,
            chat_id,
            limit,
        )
        return [Score(user_id=r["user_id"], chat_id=r["chat_id"], value=r["value"]) for r in rows]

    async def get_rank(self, user_id: int, chat_id: int) -> int | None:
        row = await self._conn.fetchrow(
            """
            SELECT rank FROM (
                SELECT user_id,
                       RANK() OVER (ORDER BY value DESC) AS rank
                FROM scores
                WHERE chat_id = $1 AND value != 0
            ) ranked
            WHERE user_id = $2
            """,
            chat_id,
            user_id,
        )
        return int(row["rank"]) if row else None