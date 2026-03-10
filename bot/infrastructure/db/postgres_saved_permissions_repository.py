import json

import asyncpg

from bot.application.interfaces.saved_permissions_repository import ISavedPermissionsRepository


class PostgresSavedPermissionsRepository(ISavedPermissionsRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def save(self, user_id: int, chat_id: int, permissions: dict) -> None:
        await self._conn.execute(
            """
            INSERT INTO saved_permissions (user_id, chat_id, permissions)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET permissions = EXCLUDED.permissions,
                    saved_at = now()
            """,
            user_id,
            chat_id,
            json.dumps(permissions),
        )

    async def get(self, user_id: int, chat_id: int) -> dict | None:
        row = await self._conn.fetchval(
            "SELECT permissions FROM saved_permissions WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )
        if row is None:
            return None
        return json.loads(row) if isinstance(row, str) else row

    async def delete(self, user_id: int, chat_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM saved_permissions WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )