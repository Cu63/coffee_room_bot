"""Атомарность операций с баллами на настоящем PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from bot.application.dice_service import DiceService
from bot.domain.ledger import LedgerKind
from bot.infrastructure.db.postgres_dice_repository import PostgresDiceRepository
from bot.infrastructure.db.postgres_score_repository import PostgresScoreRepository
from bot.infrastructure.db.postgres_user_stats_repository import PostgresUserStatsRepository
from tests.integration.helpers import CHAT, balance, seed_users

K = LedgerKind.ADMIN

A, B = 101, 102


async def run_concurrently(pool, operation, *, hold: float = 0.3):
    """Две одинаковые операции в параллельных транзакциях.

    Первая держит транзакцию открытой (как хендлер, ждущий Telegram), вторая
    стартует сразу — так воспроизводится гонка «прочитали баланс до коммита соседа».
    """

    async def one(delay: float):
        async with pool.acquire() as c:
            async with c.transaction():
                result = await operation(c)
                await asyncio.sleep(delay)
                return result

    return await asyncio.gather(one(hold), one(0.0))


async def test_concurrent_spend_never_overdraws(pool, conn, score_service_factory):
    await seed_users(conn, A, balance=100)

    async def spend(c):
        return (await score_service_factory(c).spend_score(A, A, CHAT, 100, "🧪", kind=LedgerKind.PURCHASE)).success

    results = await run_concurrently(pool, spend)
    assert sorted(results) == [False, True]
    assert await balance(conn, A) == 0


async def test_concurrent_transfer_moves_money_once(pool, conn, score_service_factory):
    await seed_users(conn, A, balance=100)
    await seed_users(conn, B, balance=0)

    async def transfer(c):
        return (await score_service_factory(c).transfer_score(A, B, CHAT, 100)).success

    results = await run_concurrently(pool, transfer)
    assert sorted(results) == [False, True]
    assert (await balance(conn, A), await balance(conn, B)) == (0, 100)


async def test_transfer_conserves_total_and_records_both_sides(conn, score_service_factory):
    await seed_users(conn, A, balance=1000)
    await seed_users(conn, B, balance=0)
    svc = score_service_factory(conn)
    for _ in range(50):
        async with conn.transaction():
            assert (await svc.transfer_score(A, B, CHAT, 1)).success
    assert await balance(conn, A) + await balance(conn, B) == 1000
    legs = await conn.fetchval("SELECT count(*) FROM ledger WHERE chat_id = $1 AND kind = 'transfer'", CHAT)
    assert legs == 100  # в журнале обе стороны каждого перевода


async def test_transfer_rejects_insufficient_and_non_positive(conn, score_service_factory):
    await seed_users(conn, A, balance=10)
    await seed_users(conn, B, balance=0)
    svc = score_service_factory(conn)
    result = await svc.transfer_score(A, B, CHAT, 11)
    assert not result.success and result.current_balance == 10
    assert not (await svc.transfer_score(A, B, CHAT, 0)).success
    assert not (await svc.transfer_score(A, B, CHAT, -5)).success
    assert (await balance(conn, A), await balance(conn, B)) == (10, 0)


async def test_spend_credits_bot_and_keeps_total(conn, score_service_factory):
    bot_id = 999
    await seed_users(conn, A, bot_id, balance=50)
    result = await score_service_factory(conn).spend_score(
        A, A, CHAT, 30, "🧪", kind=LedgerKind.PURCHASE, bot_id=bot_id
    )
    assert result.success and result.new_balance == 20
    assert await balance(conn, bot_id) == 80


async def test_concurrent_debit_up_to_takes_only_what_exists(pool, conn):
    await seed_users(conn, A, balance=100)
    taken = await run_concurrently(pool, lambda c: PostgresScoreRepository(c).debit_up_to(A, CHAT, 80, kind=K))
    assert sorted(taken) == [20, 80]
    assert await balance(conn, A) == 0


async def test_debit_up_to_edges(conn):
    await seed_users(conn, A, balance=-5)
    repo = PostgresScoreRepository(conn)
    assert await repo.debit_up_to(424242, CHAT, 10, kind=K) == 0  # нет строки
    assert await repo.debit_up_to(A, CHAT, 10, kind=K) == 0  # отрицательный баланс не трогаем
    assert await repo.debit_up_to(A, CHAT, 0, kind=K) == 0
    assert await balance(conn, A) == -5
    with pytest.raises(ValueError):
        await repo.debit_up_to(A, CHAT, -1, kind=K)


async def test_try_debit_edges(conn):
    await seed_users(conn, A, balance=50)
    repo = PostgresScoreRepository(conn)
    assert await repo.try_debit(A, CHAT, 0, kind=K) == 50
    assert await repo.try_debit(424242, CHAT, 0, kind=K) == 0
    assert await repo.try_debit(424242, CHAT, 1, kind=K) is None
    assert await repo.try_debit(A, CHAT, 51, kind=K) is None
    assert await repo.try_debit(A, CHAT, 50, kind=K) == 0
    with pytest.raises(ValueError):
        await repo.try_debit(A, CHAT, -1, kind=K)


async def test_dice_double_join_charges_once(pool, conn):
    await seed_users(conn, A, B, balance=100)

    def service(c):
        return DiceService(PostgresDiceRepository(c), PostgresScoreRepository(c), PostgresUserStatsRepository(c))

    game = (await service(conn).create(CHAT, A, 10, datetime.now(timezone.utc) + timedelta(minutes=5))).game
    assert await balance(conn, A) == 90

    results = await run_concurrently(pool, lambda c: service(c).join(game.id, B))
    assert sorted(r.success for r in results) == [False, True]
    assert any(r.already_joined for r in results)
    assert await balance(conn, B) == 90
