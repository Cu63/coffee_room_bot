import asyncpg

from bot.application.interfaces.xp_repository import IXpRepository, UserXp


class PostgresXpRepository(IXpRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def add_xp(self, user_id: int, chat_id: int, amount: int) -> int:
        row = await self._conn.fetchrow(
            """
            INSERT INTO user_xp (user_id, chat_id, xp)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET xp = user_xp.xp + EXCLUDED.xp
            RETURNING xp
            """,
            user_id,
            chat_id,
            amount,
        )
        return int(row["xp"])  # type: ignore[index]

    async def get_xp(self, user_id: int, chat_id: int) -> int:
        row = await self._conn.fetchrow(
            "SELECT xp FROM user_xp WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )
        return int(row["xp"]) if row else 0

    async def top(self, chat_id: int, limit: int) -> list[UserXp]:
        rows = await self._conn.fetch(
            """
            SELECT x.user_id, x.chat_id, x.xp
            FROM user_xp x
            JOIN users u ON u.id = x.user_id
            WHERE x.chat_id = $1 AND x.xp > 0 AND NOT u.is_bot
            ORDER BY x.xp DESC
            LIMIT $2
            """,
            chat_id,
            limit,
        )
        return [UserXp(user_id=r["user_id"], chat_id=r["chat_id"], xp=r["xp"]) for r in rows]
