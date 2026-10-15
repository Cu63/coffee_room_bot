"""Банки пошаговых игр: из банка нельзя выплатить больше, чем в него положили."""

from __future__ import annotations

import pytest

from bot.application.stake_service import PotOverdraft, StakeService
from bot.domain.ledger import LedgerKind
from bot.infrastructure import ledger_audit_loop
from tests.integration.helpers import CHAT, balance, seed_users

A, B = 731, 732
REF = "bj:1"


async def pot(conn, ref: str = REF) -> int | None:
    return await conn.fetchval("SELECT amount FROM game_pots WHERE chat_id = $1 AND ref = $2", CHAT, ref)


async def test_stakes_go_through_the_pot_and_out_once(app_container, conn):
    await seed_users(conn, A, B, balance=100)
    async with app_container() as scope:
        stakes = await scope.get(StakeService)
        assert (await stakes.hold(A, CHAT, REF, 30)).success
        assert (await stakes.hold(B, CHAT, REF, 30)).success
        assert await pot(conn) is None  # банк виден только после коммита

    assert await pot(conn) == 60
    async with app_container() as scope:
        stakes = await scope.get(StakeService)
        assert await stakes.pay(A, CHAT, REF, 60, kind=LedgerKind.PRIZE) == 130
    assert (await pot(conn), await balance(conn, A), await balance(conn, B)) == (0, 130, 70)

    # Ту же игру рассчитывают второй раз: банк пуст — выплаты нет, транзакция откатывается
    with pytest.raises(PotOverdraft):
        async with app_container() as scope:
            stakes = await scope.get(StakeService)
            await stakes.pay(B, CHAT, REF, 60, kind=LedgerKind.PRIZE)
    assert (await pot(conn), await balance(conn, B)) == (0, 70)


async def test_stake_without_money_leaves_no_pot(app_container, conn):
    await seed_users(conn, A, balance=10)
    async with app_container() as scope:
        result = await (await scope.get(StakeService)).hold(A, CHAT, REF, 30)
    assert (result.success, result.current_balance) == (False, 10)
    assert (await pot(conn), await balance(conn, A)) == (None, 10)


async def test_pay_without_a_pot_is_refused(app_container, conn):
    await seed_users(conn, A)
    with pytest.raises(PotOverdraft):
        async with app_container() as scope:
            await (await scope.get(StakeService)).pay(A, CHAT, "ttt:404", 10, kind=LedgerKind.REFUND)
    assert await balance(conn, A) == 0


async def test_game_started_before_pots_pays_as_before(app_container, conn):
    # Ставки игры, начатой до деплоя, списаны мимо банка: её доигрывают без него
    await seed_users(conn, A)
    async with app_container() as scope:
        await (await scope.get(StakeService)).pay(A, CHAT, "bj:legacy", 20, kind=LedgerKind.PRIZE, pot=False)
    assert (await balance(conn, A), await pot(conn, "bj:legacy")) == (20, None)


async def test_audit_reports_stuck_pots_and_purges_empty_ones(app_container, conn):
    await seed_users(conn, A, balance=100)
    async with app_container() as scope:
        stakes = await scope.get(StakeService)
        await stakes.hold(A, CHAT, "bj:lost", 30)
        await stakes.hold(A, CHAT, "bj:done", 10)
        await stakes.hold(A, CHAT, "bj:fresh", 10)
        await stakes.pay(A, CHAT, "bj:done", 10, kind=LedgerKind.REFUND)
    await conn.execute(
        "UPDATE game_pots SET updated_at = now() - interval '8 days' WHERE ref IN ('bj:lost', 'bj:done')"
    )

    result = await ledger_audit_loop.run_audit(app_container)

    assert [(p.ref, p.amount) for p in result.stale_pots] == [("bj:lost", 30)]
    assert await pot(conn, "bj:done") is None  # пустой и старый — удалён
    assert await pot(conn, "bj:fresh") == 10
