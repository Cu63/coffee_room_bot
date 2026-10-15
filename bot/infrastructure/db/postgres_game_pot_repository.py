from __future__ import annotations

from datetime import datetime

import asyncpg

from bot.application.interfaces.game_pot_repository import GamePot, IGamePotRepository, PotTake


class PostgresGamePotRepository(IGamePotRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def put(self, chat_id: int, ref: str, amount: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO game_pots (chat_id, ref, amount) VALUES ($1, $2, $3)
            ON CONFLICT (chat_id, ref) DO UPDATE
                SET amount = game_pots.amount + EXCLUDED.amount, updated_at = now()
            """,
            chat_id,
            ref,
            amount,
        )

    async def take(self, chat_id: int, ref: str, amount: int) -> PotTake:
        # Опустевший банк остаётся (amount = 0): второй расчёт той же игры получит SHORT, а не MISSING
        taken = await self._conn.fetchval(
            """
            UPDATE game_pots SET amount = amount - $3, updated_at = now()
            WHERE chat_id = $1 AND ref = $2 AND amount >= $3
            RETURNING 1
            """,
            chat_id,
            ref,
            amount,
        )
        if taken:
            return PotTake.TAKEN
        exists = await self._conn.fetchval("SELECT 1 FROM game_pots WHERE chat_id = $1 AND ref = $2", chat_id, ref)
        return PotTake.SHORT if exists else PotTake.MISSING

    async def stale(self, idle_since: datetime) -> list[GamePot]:
        rows = await self._conn.fetch(
            """
            SELECT chat_id, ref, amount, updated_at FROM game_pots
            WHERE amount > 0 AND updated_at < $1
            ORDER BY updated_at
            """,
            idle_since,
        )
        return [GamePot(r["chat_id"], r["ref"], r["amount"], r["updated_at"]) for r in rows]

    async def purge_empty(self, idle_since: datetime) -> int:
        result = await self._conn.execute(
            "DELETE FROM game_pots WHERE amount = 0 AND updated_at < $1",
            idle_since,
        )
        return int(result.split()[-1])
