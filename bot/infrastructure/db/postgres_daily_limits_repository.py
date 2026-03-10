from datetime import date

import asyncpg

from bot.domain.entities import DailyLimits
from bot.application.interfaces.daily_limits_repository import IDailyLimitsRepository


class PostgresDailyLimitsRepository(IDailyLimitsRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get(self, user_id: int, chat_id: int, day: date) -> DailyLimits:
        row = await self._conn.fetchrow(
            """
            SELECT user_id, chat_id, date, reactions_given, score_received
            FROM daily_limits
            WHERE user_id = $1 AND chat_id = $2 AND date = $3
            """,
            user_id,
            chat_id,
            day,
        )
        if row is None:
            return DailyLimits(user_id=user_id, chat_id=chat_id, date=day)
        return DailyLimits(
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            date=row["date"],
            reactions_given=row["reactions_given"],
            score_received=row["score_received"],
        )

    async def increment_given(self, user_id: int, chat_id: int, day: date, delta: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO daily_limits (user_id, chat_id, date, reactions_given, score_received)
            VALUES ($1, $2, $3, $4, 0)
            ON CONFLICT (user_id, chat_id, date) DO UPDATE
                SET reactions_given = daily_limits.reactions_given + $4
            """,
            user_id,
            chat_id,
            day,
            delta,
        )

    async def increment_received(self, user_id: int, chat_id: int, day: date, delta: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO daily_limits (user_id, chat_id, date, score_received, reactions_given)
            VALUES ($1, $2, $3, $4, 0)
            ON CONFLICT (user_id, chat_id, date) DO UPDATE
                SET score_received = daily_limits.score_received + $4
            """,
            user_id,
            chat_id,
            day,
            delta,
        )
