from __future__ import annotations

from datetime import datetime

import asyncpg

from bot.application.interfaces.dice_repository import IDiceRepository
from bot.domain.dice_entities import DiceGame, DiceGameStatus


class PostgresDiceRepository(IDiceRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(self, game: DiceGame) -> DiceGame:
        row = await self._conn.fetchrow(
            """
            INSERT INTO dice_games (chat_id, bet, ends_at, created_by)
            VALUES ($1, $2, $3, $4)
            RETURNING id, created_at
            """,
            game.chat_id,
            game.bet,
            game.ends_at,
            game.created_by,
        )
        game.id = row["id"]
        game.created_at = row["created_at"]
        return game

    async def update_message_id(self, game_id: int, message_id: int) -> None:
        await self._conn.execute(
            "UPDATE dice_games SET message_id = $1 WHERE id = $2",
            message_id,
            game_id,
        )

    async def get(self, game_id: int) -> DiceGame | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM dice_games WHERE id = $1",
            game_id,
        )
        return self._row_to_game(row) if row else None

    async def get_pending_in_chat(self, chat_id: int) -> DiceGame | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM dice_games WHERE chat_id = $1 AND status = 'pending' LIMIT 1",
            chat_id,
        )
        return self._row_to_game(row) if row else None

    async def finish(self, game_id: int) -> None:
        await self._conn.execute(
            "UPDATE dice_games SET status = 'finished' WHERE id = $1",
            game_id,
        )

    async def get_expired(self, now: datetime) -> list[DiceGame]:
        rows = await self._conn.fetch(
            """
            SELECT * FROM dice_games
            WHERE status = 'pending' AND ends_at <= $1
            """,
            now,
        )
        return [self._row_to_game(r) for r in rows]

    async def add_participant(self, game_id: int, user_id: int) -> bool:
        try:
            await self._conn.execute(
                """
                INSERT INTO dice_participants (game_id, user_id)
                VALUES ($1, $2)
                """,
                game_id,
                user_id,
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

    async def get_participants(self, game_id: int) -> list[int]:
        rows = await self._conn.fetch(
            "SELECT user_id FROM dice_participants WHERE game_id = $1",
            game_id,
        )
        return [r["user_id"] for r in rows]

    async def count_participants(self, game_id: int) -> int:
        row = await self._conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM dice_participants WHERE game_id = $1",
            game_id,
        )
        return row["cnt"] if row else 0

    async def is_user_in_active_game(self, chat_id: int, user_id: int) -> bool:
        row = await self._conn.fetchrow(
            """
            SELECT 1 FROM dice_participants dp
            JOIN dice_games dg ON dp.game_id = dg.id
            WHERE dg.chat_id = $1 AND dp.user_id = $2 AND dg.status = 'pending'
            LIMIT 1
            """,
            chat_id,
            user_id,
        )
        return row is not None

    @staticmethod
    def _row_to_game(row: asyncpg.Record) -> DiceGame:
        return DiceGame(
            id=row["id"],
            chat_id=row["chat_id"],
            bet=row["bet"],
            status=DiceGameStatus(row["status"]),
            message_id=row["message_id"],
            ends_at=row["ends_at"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )
