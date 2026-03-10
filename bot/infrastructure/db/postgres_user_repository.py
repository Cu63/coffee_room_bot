import asyncpg

from bot.domain.entities import User
from bot.application.interfaces.user_repository import IUserRepository


class PostgresUserRepository(IUserRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def upsert(self, user: User) -> None:
        await self._conn.execute(
            """
            INSERT INTO users (id, username, full_name, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (id) DO UPDATE
                SET username  = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    updated_at = now()
            """,
            user.id,
            user.username,
            user.full_name,
        )

    async def get_by_username(self, username: str) -> User | None:
        row = await self._conn.fetchrow(
            "SELECT id, username, full_name FROM users WHERE username = $1",
            username,
        )
        return self._to_entity(row)

    async def get_by_id(self, user_id: int) -> User | None:
        row = await self._conn.fetchrow(
            "SELECT id, username, full_name FROM users WHERE id = $1",
            user_id,
        )
        return self._to_entity(row)

    @staticmethod
    def _to_entity(row: asyncpg.Record | None) -> User | None:
        if row is None:
            return None
        return User(id=row["id"], username=row["username"], full_name=row["full_name"])
