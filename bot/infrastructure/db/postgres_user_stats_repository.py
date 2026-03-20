from asyncpg import Connection

from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.domain.entities import UserStats

_ALLOWED_GAMES = {"blackjack", "slots", "dice", "giveaway", "ttt"}


class PostgresUserStatsRepository(IUserStatsRepository):
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    async def get(self, user_id: int, chat_id: int) -> UserStats:
        row = await self._conn.fetchrow(
            """
            SELECT score_given, score_taken,
                   wins_blackjack, wins_slots, wins_dice, wins_giveaway, wins_ttt
            FROM user_stats
            WHERE user_id = $1 AND chat_id = $2
            """,
            user_id,
            chat_id,
        )
        if row is None:
            return UserStats(user_id=user_id, chat_id=chat_id)
        return UserStats(
            user_id=user_id,
            chat_id=chat_id,
            score_given=row["score_given"],
            score_taken=row["score_taken"],
            wins_blackjack=row["wins_blackjack"],
            wins_slots=row["wins_slots"],
            wins_dice=row["wins_dice"],
            wins_giveaway=row["wins_giveaway"],
            wins_ttt=row["wins_ttt"],
        )

    async def add_score_given(self, user_id: int, chat_id: int, delta: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO user_stats (user_id, chat_id, score_given)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET score_given = user_stats.score_given + EXCLUDED.score_given
            """,
            user_id,
            chat_id,
            delta,
        )

    async def add_score_taken(self, user_id: int, chat_id: int, delta: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO user_stats (user_id, chat_id, score_taken)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET score_taken = user_stats.score_taken + EXCLUDED.score_taken
            """,
            user_id,
            chat_id,
            delta,
        )

    async def add_win(self, user_id: int, chat_id: int, game: str) -> None:
        if game not in _ALLOWED_GAMES:
            raise ValueError(f"Unknown game: {game!r}")
        column = f"wins_{game}"
        await self._conn.execute(
            f"""
            INSERT INTO user_stats (user_id, chat_id, {column})
            VALUES ($1, $2, 1)
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET {column} = user_stats.{column} + 1
            """,
            user_id,
            chat_id,
        )
