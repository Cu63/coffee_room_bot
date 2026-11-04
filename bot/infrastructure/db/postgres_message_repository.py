from __future__ import annotations

from datetime import datetime

import asyncpg

from bot.application.interfaces.message_repository import (
    ChatMessage,
    IMessageRepository,
    MessageInfo,
)
from bot.domain.entities import User


class PostgresMessageRepository(IMessageRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def delete_before(self, cutoff: datetime) -> int:
        result = await self._conn.execute("DELETE FROM messages WHERE sent_at < $1", cutoff)
        return int(result.split()[-1])

    async def save(self, info: MessageInfo) -> None:
        await self._conn.execute(
            """
            INSERT INTO messages (message_id, chat_id, user_id, sent_at, text, is_reply)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (message_id, chat_id) DO UPDATE
                SET text = COALESCE(EXCLUDED.text, messages.text),
                    is_reply = EXCLUDED.is_reply OR messages.is_reply
            """,
            info.message_id,
            info.chat_id,
            info.user_id,
            info.sent_at,
            info.text,
            info.is_reply,
        )

    async def get(self, chat_id: int, message_id: int) -> MessageInfo | None:
        row = await self._conn.fetchrow(
            """
            SELECT message_id, chat_id, user_id, sent_at, text
            FROM messages
            WHERE chat_id = $1 AND message_id = $2
            """,
            chat_id,
            message_id,
        )
        if row is None:
            return None
        return MessageInfo(
            message_id=row["message_id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            sent_at=row["sent_at"],
            text=row["text"],
        )

    async def get_recent_with_text(
        self,
        chat_id: int,
        limit: int,
        user_ids: list[int] | None = None,
        since: datetime | None = None,
    ) -> list[ChatMessage]:
        """Вернуть до ``limit`` последних сообщений с непустым текстом.

        Фильтры:
        - ``user_ids`` — только эти пользователи (None = все)
        - ``since`` — только сообщения новее этого момента (None = без ограничения)
        """
        if user_ids is not None and len(user_ids) == 0:
            return []

        rows = await self._conn.fetch(
            """
            SELECT sub.message_id,
                   sub.user_id,
                   sub.username,
                   sub.full_name,
                   sub.text,
                   sub.sent_at
            FROM (
                SELECT m.message_id,
                       m.user_id,
                       u.username,
                       u.full_name,
                       m.text,
                       m.sent_at
                FROM messages m
                JOIN users u ON u.id = m.user_id
                WHERE m.chat_id = $1
                  AND m.text IS NOT NULL
                  AND ($3::BIGINT[] IS NULL OR m.user_id = ANY($3))
                  AND ($4::TIMESTAMPTZ IS NULL OR m.sent_at >= $4)
                ORDER BY m.sent_at DESC
                LIMIT $2
            ) sub
            ORDER BY sub.sent_at ASC
            """,
            chat_id,
            limit,
            user_ids,
            since,
        )

        return [
            ChatMessage(
                message_id=row["message_id"],
                user_id=row["user_id"],
                username=row["username"],
                full_name=row["full_name"],
                text=row["text"],
                sent_at=row["sent_at"],
            )
            for row in rows
        ]

    async def random_quote(self, chat_id: int, min_length: int, max_length: int, since: datetime) -> ChatMessage | None:
        row = await self._conn.fetchrow(
            """
            SELECT m.message_id, m.user_id, u.username, u.full_name, m.text, m.sent_at
            FROM messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.chat_id = $1
              AND m.sent_at >= $4
              AND NOT u.is_bot
              AND char_length(m.text) BETWEEN $2 AND $3
              AND m.text NOT LIKE '/%'
            ORDER BY random()
            LIMIT 1
            """,
            chat_id,
            min_length,
            max_length,
            since,
        )
        return ChatMessage(**dict(row)) if row else None

    async def random_authors(self, chat_id: int, exclude_user_id: int, since: datetime, limit: int) -> list[User]:
        rows = await self._conn.fetch(
            """
            SELECT u.id, u.username, u.full_name
            FROM (
                SELECT DISTINCT user_id FROM messages
                WHERE chat_id = $1 AND sent_at >= $3 AND text IS NOT NULL AND user_id <> $2
            ) a
            JOIN users u ON u.id = a.user_id
            WHERE NOT u.is_bot
            ORDER BY random()
            LIMIT $4
            """,
            chat_id,
            exclude_user_id,
            since,
            limit,
        )
        return [User(id=r["id"], username=r["username"], full_name=r["full_name"]) for r in rows]

    async def get_active_chats(self) -> list[int]:
        rows = await self._conn.fetch("SELECT DISTINCT chat_id FROM messages WHERE text IS NOT NULL AND chat_id < 0")
        return [r["chat_id"] for r in rows]
