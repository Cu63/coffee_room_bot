from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import asyncpg

from bot.application.interfaces.daily_leaderboard_repository import (
    DailyLeader,
    DailyLeaderboard,
    IDailyLeaderboardRepository,
)

_TZ_MSK = timezone(timedelta(hours=3))

_ALLOWED_GAMES = {"ttt", "word", "rword"}


def _day_bounds(for_date: date) -> tuple[datetime, datetime]:
    """Возвращает [start, end) для дня в MSK как aware-datetime."""
    start = datetime(for_date.year, for_date.month, for_date.day, 0, 0, 0, tzinfo=_TZ_MSK)
    end = start + timedelta(days=1)
    return start, end


class PostgresDailyLeaderboardRepository(IDailyLeaderboardRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ──────────────────────────────────────────────────────────────────────────
    # Публичные методы
    # ──────────────────────────────────────────────────────────────────────────

    async def get_daily_leaderboard(self, chat_id: int, for_date: date) -> DailyLeaderboard:
        start, end = _day_bounds(for_date)

        top_msg = await self._top_messages(chat_id, start, end)
        top_rg = await self._top_reactions_given(chat_id, start, end)
        top_rr = await self._top_reactions_received(chat_id, start, end)
        top_rep = await self._top_replies(chat_id, start, end)
        top_ttt = await self._top_ttt_wins(chat_id, for_date)
        top_wg = await self._top_wordgame_wins(chat_id, for_date)
        top_mg = await self._top_mutes_given(chat_id, start, end)
        top_mr = await self._top_mutes_received(chat_id, start, end)

        return DailyLeaderboard(
            date=for_date,
            top_messages=top_msg,
            top_reactions_given=top_rg,
            top_reactions_received=top_rr,
            top_replies=top_rep,
            top_ttt_wins=top_ttt,
            top_wordgame_wins=top_wg,
            top_mutes_given=top_mg,
            top_mutes_received=top_mr,
        )

    async def add_game_win(
        self,
        user_id: int,
        chat_id: int,
        game: str,
        for_date: date,
    ) -> None:
        if game not in _ALLOWED_GAMES:
            raise ValueError(f"Unknown game: {game!r}")
        column = f"{game}_wins"
        await self._conn.execute(
            f"""
            INSERT INTO daily_game_wins (user_id, chat_id, date, {column})
            VALUES ($1, $2, $3, 1)
            ON CONFLICT (user_id, chat_id, date) DO UPDATE
                SET {column} = daily_game_wins.{column} + 1
            """,
            user_id,
            chat_id,
            for_date,
        )

    async def get_active_chats(self) -> list[int]:
        rows = await self._conn.fetch(
            "SELECT DISTINCT chat_id FROM messages WHERE text IS NOT NULL AND chat_id < 0"
        )
        return [r["chat_id"] for r in rows]

    # ──────────────────────────────────────────────────────────────────────────
    # Приватные запросы
    # ──────────────────────────────────────────────────────────────────────────

    async def _top_messages(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT m.user_id, u.username, u.full_name, COUNT(*) AS cnt
            FROM messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.chat_id = $1
              AND m.sent_at >= $2
              AND m.sent_at < $3
              AND m.text IS NOT NULL
              AND NOT u.is_bot
            GROUP BY m.user_id, u.username, u.full_name
            ORDER BY cnt DESC
            LIMIT 1
            """,
            chat_id, start, end,
        )
        return _row_to_leader(row)

    async def _top_reactions_given(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT se.actor_id AS user_id, u.username, u.full_name, COUNT(*) AS cnt
            FROM score_events se
            JOIN users u ON u.id = se.actor_id
            WHERE se.chat_id = $1
              AND se.created_at >= $2
              AND se.created_at < $3
              AND se.direction = 'ADD'
              AND se.actor_id != se.target_id
              AND se.delta > 0
              AND NOT u.is_bot
            GROUP BY se.actor_id, u.username, u.full_name
            ORDER BY cnt DESC
            LIMIT 1
            """,
            chat_id, start, end,
        )
        return _row_to_leader(row)

    async def _top_reactions_received(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT se.target_id AS user_id, u.username, u.full_name, COUNT(*) AS cnt
            FROM score_events se
            JOIN users u ON u.id = se.target_id
            WHERE se.chat_id = $1
              AND se.created_at >= $2
              AND se.created_at < $3
              AND se.direction = 'ADD'
              AND se.actor_id != se.target_id
              AND se.delta > 0
              AND NOT u.is_bot
            GROUP BY se.target_id, u.username, u.full_name
            ORDER BY cnt DESC
            LIMIT 1
            """,
            chat_id, start, end,
        )
        return _row_to_leader(row)

    async def _top_replies(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT m.user_id, u.username, u.full_name, COUNT(*) AS cnt
            FROM messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.chat_id = $1
              AND m.sent_at >= $2
              AND m.sent_at < $3
              AND m.is_reply = TRUE
              AND NOT u.is_bot
            GROUP BY m.user_id, u.username, u.full_name
            ORDER BY cnt DESC
            LIMIT 1
            """,
            chat_id, start, end,
        )
        return _row_to_leader(row)

    async def _top_ttt_wins(self, chat_id: int, for_date: date) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT dw.user_id, u.username, u.full_name, dw.ttt_wins AS cnt
            FROM daily_game_wins dw
            JOIN users u ON u.id = dw.user_id
            WHERE dw.chat_id = $1
              AND dw.date = $2
              AND dw.ttt_wins > 0
            ORDER BY dw.ttt_wins DESC
            LIMIT 1
            """,
            chat_id, for_date,
        )
        return _row_to_leader(row)

    async def _top_wordgame_wins(self, chat_id: int, for_date: date) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT dw.user_id, u.username, u.full_name,
                   (dw.word_wins + dw.rword_wins) AS cnt
            FROM daily_game_wins dw
            JOIN users u ON u.id = dw.user_id
            WHERE dw.chat_id = $1
              AND dw.date = $2
              AND (dw.word_wins + dw.rword_wins) > 0
            ORDER BY cnt DESC
            LIMIT 1
            """,
            chat_id, for_date,
        )
        return _row_to_leader(row)


    async def _top_mutes_given(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT mh.muted_by AS user_id, u.username, u.full_name, COUNT(*) AS cnt
            FROM mute_history mh
            JOIN users u ON u.id = mh.muted_by
            WHERE mh.chat_id = $1
              AND mh.created_at >= $2
              AND mh.created_at < $3
              AND mh.muted_by != mh.user_id
              AND NOT u.is_bot
            GROUP BY mh.muted_by, u.username, u.full_name
            ORDER BY cnt DESC
            LIMIT 1
            """,
            chat_id, start, end,
        )
        return _row_to_leader(row)

    async def _top_mutes_received(
        self,
        chat_id: int,
        start: datetime,
        end: datetime,
    ) -> DailyLeader | None:
        row = await self._conn.fetchrow(
            """
            SELECT mh.user_id, u.username, u.full_name, COUNT(*) AS cnt
            FROM mute_history mh
            JOIN users u ON u.id = mh.user_id
            WHERE mh.chat_id = $1
              AND mh.created_at >= $2
              AND mh.created_at < $3
              AND NOT u.is_bot
            GROUP BY mh.user_id, u.username, u.full_name
            ORDER BY cnt DESC
            LIMIT 1
            """,
            chat_id, start, end,
        )
        return _row_to_leader(row)


def _row_to_leader(row) -> DailyLeader | None:
    if row is None or row["cnt"] == 0:
        return None
    return DailyLeader(
        user_id=row["user_id"],
        username=row["username"],
        full_name=row["full_name"],
        value=row["cnt"],
    )
