import asyncpg

from bot.application.interfaces.llm_repository import ILlmRepository


class PostgresLlmRepository(ILlmRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def count_today(self, user_id: int) -> int:
        return await self._conn.fetchval(
            """
            SELECT COUNT(*) FROM llm_requests
            WHERE user_id = $1 AND created_at >= CURRENT_DATE
            """,
            user_id,
        )

    async def log_request(
        self,
        user_id: int,
        chat_id: int,
        command: str,
        query: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO llm_requests (user_id, chat_id, command, query, input_tokens, output_tokens)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            user_id,
            chat_id,
            command,
            query,
            input_tokens,
            output_tokens,
        )
