import asyncpg

from bot.application.interfaces.message_repository import (
    ChatMessage,
    IMessageRepository,
    MessageInfo,
)


class PostgresMessageRepository(IMessageRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def save(self, info: MessageInfo) -> None:
        await self._conn.execute(
            """
            INSERT INTO messages (message_id, chat_id, user_id, sent_at, text)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (message_id, chat_id) DO UPDATE
                SET text = COALESCE(EXCLUDED.text, messages.text)
            """,
            info.message_id,
            info.chat_id,
            info.user_id,
            info.sent_at,
            info.text,
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
    ) -> list[ChatMessage]:
        """Вернуть до ``limit`` последних сообщений с непустым текстом.

        Запрос делается через подзапрос: сначала берём последние N строк
        (ORDER BY sent_at DESC, LIMIT), а потом разворачиваем в хронологию.
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
                ORDER BY m.sent_at DESC
                LIMIT $2
            ) sub
            ORDER BY sub.sent_at ASC
            """,
            chat_id,
            limit,
            user_ids,  # передаём None или список — asyncpg обрабатывает оба варианта
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
