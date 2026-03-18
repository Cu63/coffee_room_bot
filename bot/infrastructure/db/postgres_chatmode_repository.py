from __future__ import annotations

import json
from datetime import datetime

import asyncpg

from bot.application.interfaces.chatmode_repository import ChatmodeEntry, IChatmodeRepository


class PostgresChatmodeRepository(IChatmodeRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get(self, chat_id: int) -> ChatmodeEntry | None:
        row = await self._conn.fetchrow(
            """
            SELECT chat_id, mode, activated_by, activated_at, expires_at, saved_perms
            FROM chatmode
            WHERE chat_id = $1
            """,
            chat_id,
        )
        return self._to_entry(row)

    async def save(self, entry: ChatmodeEntry) -> None:
        await self._conn.execute(
            """
            INSERT INTO chatmode (chat_id, mode, activated_by, activated_at, expires_at, saved_perms)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (chat_id) DO UPDATE
                SET mode         = EXCLUDED.mode,
                    activated_by = EXCLUDED.activated_by,
                    activated_at = EXCLUDED.activated_at,
                    expires_at   = EXCLUDED.expires_at,
                    saved_perms  = EXCLUDED.saved_perms
            """,
            entry.chat_id,
            entry.mode,
            entry.activated_by,
            entry.activated_at,
            entry.expires_at,
            json.dumps(entry.saved_perms),
        )

    async def delete(self, chat_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM chatmode WHERE chat_id = $1",
            chat_id,
        )

    async def get_expired(self, now: datetime) -> list[ChatmodeEntry]:
        rows = await self._conn.fetch(
            """
            SELECT chat_id, mode, activated_by, activated_at, expires_at, saved_perms
            FROM chatmode
            WHERE expires_at <= $1
            """,
            now,
        )
        return [self._to_entry(r) for r in rows]  # type: ignore[misc]

    @staticmethod
    def _to_entry(row: asyncpg.Record | None) -> ChatmodeEntry | None:
        if row is None:
            return None
        perms = row["saved_perms"]
        if isinstance(perms, str):
            perms = json.loads(perms)
        return ChatmodeEntry(
            chat_id=row["chat_id"],
            mode=row["mode"],
            activated_by=row["activated_by"],
            activated_at=row["activated_at"],
            expires_at=row["expires_at"],
            saved_perms=perms,
        )
