import asyncpg

from bot.application.interfaces.message_repository import IMessageRepository, MessageInfo


class PostgresMessageRepository(IMessageRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def save(self, info: MessageInfo) -> None:
        await self._conn.execute(
            """
            INSERT INTO messages (message_id, chat_id, user_id, sent_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (message_id, chat_id) DO NOTHING
            """,
            info.message_id,
            info.chat_id,
            info.user_id,
            info.sent_at,
        )

    async def get(self, chat_id: int, message_id: int) -> MessageInfo | None:
        row = await self._conn.fetchrow(
            """
            SELECT message_id, chat_id, user_id, sent_at
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
        )
