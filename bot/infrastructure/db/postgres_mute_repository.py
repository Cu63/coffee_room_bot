import json
from datetime import datetime

import asyncpg

from bot.application.interfaces.mute_repository import IMuteRepository
from bot.domain.entities import MuteEntry


class PostgresMuteRepository(IMuteRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def save(self, entry: MuteEntry) -> None:
        perms_json = json.dumps(entry.admin_permissions) if entry.admin_permissions else None
        await self._conn.execute(
            """
            INSERT INTO active_mutes (user_id, chat_id, muted_by, until_at, was_admin, admin_permissions)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET muted_by = EXCLUDED.muted_by,
                    until_at = EXCLUDED.until_at,
                    was_admin = EXCLUDED.was_admin,
                    admin_permissions = EXCLUDED.admin_permissions,
                    created_at = now()
            """,
            entry.user_id,
            entry.chat_id,
            entry.muted_by,
            entry.until_at,
            entry.was_admin,
            perms_json,
        )

    async def get(self, user_id: int, chat_id: int) -> MuteEntry | None:
        row = await self._conn.fetchrow(
            "SELECT user_id, chat_id, muted_by, until_at, was_admin, admin_permissions "
            "FROM active_mutes WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )
        return self._to_entity(row) if row else None

    async def delete(self, user_id: int, chat_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM active_mutes WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )

    async def get_expired(self, now: datetime) -> list[MuteEntry]:
        rows = await self._conn.fetch(
            "SELECT user_id, chat_id, muted_by, until_at, was_admin, admin_permissions "
            "FROM active_mutes WHERE until_at <= $1",
            now,
        )
        return [self._to_entity(r) for r in rows]

    async def log_mute(self, user_id: int, muted_by: int, chat_id: int) -> None:
        await self._conn.execute(
            "INSERT INTO mute_history (chat_id, user_id, muted_by) VALUES ($1, $2, $3)",
            chat_id,
            user_id,
            muted_by,
        )

    @staticmethod
    def _to_entity(row: asyncpg.Record) -> MuteEntry:
        perms = row["admin_permissions"]
        if isinstance(perms, str):
            perms = json.loads(perms)
        return MuteEntry(
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            muted_by=row["muted_by"],
            until_at=row["until_at"],
            was_admin=row["was_admin"],
            admin_permissions=perms,
        )
