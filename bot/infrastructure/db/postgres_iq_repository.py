import asyncpg

from bot.application.interfaces.iq_repository import IIqRepository, UserIq


class PostgresIqRepository(IIqRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_iq(self, user_id: int, chat_id: int) -> int | None:
        row = await self._conn.fetchrow(
            "SELECT iq FROM user_iq WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )
        return int(row["iq"]) if row else None

    async def add_iq(self, user_id: int, chat_id: int, delta: int, default: int) -> int:
        row = await self._conn.fetchrow(
            """
            INSERT INTO user_iq (user_id, chat_id, iq)
            VALUES ($1, $2, $4::bigint + $3::bigint)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET iq = user_iq.iq + $3::bigint
            RETURNING iq
            """,
            user_id,
            chat_id,
            delta,
            default,
        )
        return int(row["iq"])  # type: ignore[index]

    async def set_iq(self, user_id: int, chat_id: int, value: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO user_iq (user_id, chat_id, iq)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET iq = $3
            """,
            user_id,
            chat_id,
            value,
        )

    async def top(self, chat_id: int, limit: int) -> list[UserIq]:
        rows = await self._conn.fetch(
            """
            SELECT i.user_id, i.chat_id, i.iq
            FROM user_iq i
            JOIN users u ON u.id = i.user_id
            WHERE i.chat_id = $1 AND NOT u.is_bot
            ORDER BY i.iq DESC
            LIMIT $2
            """,
            chat_id,
            limit,
        )
        return [UserIq(user_id=r["user_id"], chat_id=r["chat_id"], iq=r["iq"]) for r in rows]
