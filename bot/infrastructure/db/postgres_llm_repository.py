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

    async def sum_input_tokens_today(
        self,
        user_id: int,
        commands: tuple[str, ...],
    ) -> int:
        """Сумма input_tokens пользователя за сегодня по указанным командам."""
        return await self._conn.fetchval(
            """
            SELECT COALESCE(SUM(input_tokens), 0) FROM llm_requests
            WHERE user_id = $1
              AND command = ANY($2)
              AND created_at >= CURRENT_DATE
            """,
            user_id,
            list(commands),
        )

    async def sum_tokens_global_today(
        self,
        commands: tuple[str, ...],
    ) -> tuple[int, int]:
        """Глобальная сумма (input_tokens, output_tokens) за сегодня."""
        row = await self._conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0)  AS input_total,
                COALESCE(SUM(output_tokens), 0) AS output_total
            FROM llm_requests
            WHERE command = ANY($1)
              AND created_at >= CURRENT_DATE
            """,
            list(commands),
        )
        return int(row["input_total"]), int(row["output_total"])
