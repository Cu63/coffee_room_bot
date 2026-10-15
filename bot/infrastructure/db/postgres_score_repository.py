import asyncpg

from bot.application.interfaces.score_repository import IScoreRepository
from bot.domain.entities import Score
from bot.domain.ledger import LedgerKind

# Тип операции для триггера журнала (V017__ledger.sql). Ставится в той же команде,
# что и изменение баланса, поэтому работает и в транзакции, и в autocommit.
# Параметры $4–$6: kind, ref, counterparty_id.
_LEDGER_CTX = """ctx AS MATERIALIZED (
                SELECT set_config('ledger.kind', $4, true) AS kind,
                       set_config('ledger.ref', $5, true) AS ref,
                       set_config('ledger.counterparty', $6, true) AS counterparty
            )"""


class PostgresScoreRepository(IScoreRepository):
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get(self, user_id: int, chat_id: int) -> Score | None:
        row = await self._conn.fetchrow(
            "SELECT user_id, chat_id, value FROM scores WHERE user_id = $1 AND chat_id = $2",
            user_id,
            chat_id,
        )
        if row is None:
            return None
        return Score(user_id=row["user_id"], chat_id=row["chat_id"], value=row["value"])

    @staticmethod
    def _ledger_args(kind: LedgerKind, ref: str | None, counterparty_id: int | None) -> tuple[str, str, str]:
        return str(kind), ref or "", "" if counterparty_id is None else str(counterparty_id)

    async def add_delta(
        self,
        user_id: int,
        chat_id: int,
        delta: int,
        *,
        kind: LedgerKind,
        ref: str | None = None,
        counterparty_id: int | None = None,
    ) -> int:
        row = await self._conn.fetchrow(
            f"""
            WITH {_LEDGER_CTX}
            INSERT INTO scores (user_id, chat_id, value)
            SELECT $1::bigint, $2::bigint, $3::int FROM ctx
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET value = scores.value + EXCLUDED.value
            RETURNING value
            """,
            user_id,
            chat_id,
            delta,
            *self._ledger_args(kind, ref, counterparty_id),
        )
        return row["value"]  # type: ignore[index]

    async def try_debit(
        self,
        user_id: int,
        chat_id: int,
        amount: int,
        *,
        kind: LedgerKind,
        ref: str | None = None,
        counterparty_id: int | None = None,
    ) -> int | None:
        if amount < 0:
            raise ValueError(f"debit amount must be non-negative, got {amount}")
        if amount == 0:
            score = await self.get(user_id, chat_id)
            return score.value if score else 0
        return await self._conn.fetchval(
            f"""
            WITH {_LEDGER_CTX}
            UPDATE scores SET value = value - $3::int
            FROM ctx
            WHERE user_id = $1 AND chat_id = $2 AND value >= $3::int
            RETURNING value
            """,
            user_id,
            chat_id,
            amount,
            *self._ledger_args(kind, ref, counterparty_id),
        )

    async def debit_up_to(
        self,
        user_id: int,
        chat_id: int,
        amount: int,
        *,
        kind: LedgerKind,
        ref: str | None = None,
        counterparty_id: int | None = None,
    ) -> int:
        if amount < 0:
            raise ValueError(f"debit amount must be non-negative, got {amount}")
        if amount == 0:
            return 0
        # FOR UPDATE блокирует строку до UPDATE: списываемая сумма считается
        # от актуального баланса, а не от прочитанного раньше.
        taken = await self._conn.fetchval(
            f"""
            WITH {_LEDGER_CTX},
            cur AS (
                SELECT LEAST($3::int, GREATEST(value, 0)) AS taken
                FROM scores
                WHERE user_id = $1 AND chat_id = $2
                FOR UPDATE
            )
            UPDATE scores s SET value = s.value - cur.taken
            FROM cur, ctx
            WHERE s.user_id = $1 AND s.chat_id = $2
            RETURNING cur.taken
            """,
            user_id,
            chat_id,
            amount,
            *self._ledger_args(kind, ref, counterparty_id),
        )
        return taken or 0

    async def set_value(
        self,
        user_id: int,
        chat_id: int,
        value: int,
        *,
        kind: LedgerKind,
        ref: str | None = None,
        counterparty_id: int | None = None,
    ) -> int:
        row = await self._conn.fetchrow(
            f"""
            WITH {_LEDGER_CTX}
            INSERT INTO scores (user_id, chat_id, value)
            SELECT $1::bigint, $2::bigint, $3::int FROM ctx
            ON CONFLICT (user_id, chat_id) DO UPDATE
                SET value = EXCLUDED.value
            RETURNING value
            """,
            user_id,
            chat_id,
            value,
            *self._ledger_args(kind, ref, counterparty_id),
        )
        return row["value"]  # type: ignore[index]

    async def top(self, chat_id: int, limit: int) -> list[Score]:
        rows = await self._conn.fetch(
            """
            SELECT s.user_id, s.chat_id, s.value
            FROM scores s
            JOIN users u ON u.id = s.user_id
            WHERE s.chat_id = $1 AND s.value != 0 AND NOT u.is_bot
            ORDER BY s.value DESC
            LIMIT $2
            """,
            chat_id,
            limit,
        )
        return [Score(user_id=r["user_id"], chat_id=r["chat_id"], value=r["value"]) for r in rows]

    async def bottom(self, chat_id: int, limit: int) -> list[Score]:
        rows = await self._conn.fetch(
            """
            SELECT s.user_id, s.chat_id, s.value
            FROM scores s
            JOIN users u ON u.id = s.user_id
            WHERE s.chat_id = $1 AND s.value != 0 AND NOT u.is_bot
            ORDER BY s.value ASC
            LIMIT $2
            """,
            chat_id,
            limit,
        )
        return [Score(user_id=r["user_id"], chat_id=r["chat_id"], value=r["value"]) for r in rows]

    async def get_all_user_ids(self, chat_id: int) -> list[int]:
        rows = await self._conn.fetch(
            """
            SELECT s.user_id
            FROM scores s
            JOIN users u ON u.id = s.user_id
            WHERE s.chat_id = $1 AND s.value != 0 AND NOT u.is_bot
            """,
            chat_id,
        )
        return [r["user_id"] for r in rows]

    async def get_rich_users(self, chat_id: int, min_balance: int) -> list[Score]:
        rows = await self._conn.fetch(
            """
            SELECT s.user_id, s.chat_id, s.value
            FROM scores s
            JOIN users u ON u.id = s.user_id
            WHERE s.chat_id = $1 AND s.value >= $2 AND NOT u.is_bot
            ORDER BY s.value DESC
            """,
            chat_id,
            min_balance,
        )
        return [Score(user_id=r["user_id"], chat_id=r["chat_id"], value=r["value"]) for r in rows]

    async def get_random_user(self, chat_id: int, exclude_id: int) -> tuple[int, str | None, str] | None:
        row = await self._conn.fetchrow(
            """
            SELECT u.id, u.username, u.full_name
            FROM scores s
            JOIN users u ON u.id = s.user_id
            WHERE s.chat_id = $1 AND s.value != 0 AND NOT u.is_bot AND s.user_id != $2
            ORDER BY random()
            LIMIT 1
            """,
            chat_id,
            exclude_id,
        )
        if row is None:
            return None
        return (row["id"], row["username"], row["full_name"])

    async def get_rank(self, user_id: int, chat_id: int) -> int | None:
        row = await self._conn.fetchrow(
            """
            SELECT rank FROM (
                SELECT s.user_id,
                       RANK() OVER (ORDER BY s.value DESC) AS rank
                FROM scores s
                JOIN users u ON u.id = s.user_id
                WHERE s.chat_id = $1 AND s.value != 0 AND NOT u.is_bot
            ) ranked
            WHERE user_id = $2
            """,
            chat_id,
            user_id,
        )
        return int(row["rank"]) if row else None
