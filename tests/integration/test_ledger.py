"""Журнал операций: каждое изменение баланса записано с типом, сверка ловит правки мимо кода."""

from __future__ import annotations

from datetime import datetime

import pytest

from bot.application.interfaces.message_repository import MessageInfo
from bot.application.score_service import SPECIAL_EMOJI
from bot.domain.ledger import LedgerKind
from bot.domain.tz import TZ_MSK
from bot.infrastructure import ledger_audit_loop
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from tests.integration.helpers import BOT_ID, CHAT, seed_users

A, B = 901, 902


async def journal(conn, user_id: int) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT kind, delta, balance_after, ref, counterparty_id FROM ledger
        WHERE user_id = $1 AND kind <> 'opening' ORDER BY id
        """,
        user_id,
    )
    return [dict(r) for r in rows]


def entry(kind: LedgerKind, delta: int, after: int, ref: str | None = None, counterparty: int | None = None) -> dict:
    return {"kind": kind, "delta": delta, "balance_after": after, "ref": ref, "counterparty_id": counterparty}


async def test_transfer_is_journaled_on_both_sides(conn, score_service_factory):
    await seed_users(conn, A, balance=100)
    await seed_users(conn, B, balance=0)
    assert (await score_service_factory(conn).transfer_score(A, B, CHAT, 30)).success
    assert await journal(conn, A) == [entry(LedgerKind.TRANSFER, -30, 70, counterparty=B)]
    assert await journal(conn, B) == [entry(LedgerKind.TRANSFER, 30, 30, counterparty=A)]


async def test_purchase_is_journaled_for_buyer_and_house(conn, score_service_factory):
    await seed_users(conn, A, balance=100)
    await seed_users(conn, B)
    await seed_users(conn, BOT_ID, is_bot=True)
    result = await score_service_factory(conn).spend_score(
        A, B, CHAT, 30, SPECIAL_EMOJI["mute"], kind=LedgerKind.PURCHASE, bot_id=BOT_ID
    )
    assert result.success
    assert await journal(conn, A) == [entry(LedgerKind.PURCHASE, -30, 70, "mute", B)]
    assert await journal(conn, BOT_ID) == [entry(LedgerKind.PURCHASE, 30, 30, "mute", A)]


async def test_reaction_and_its_removal_are_journaled(conn, score_service_factory):
    await seed_users(conn, A, B)
    await PostgresMessageRepository(conn).save(
        MessageInfo(message_id=1, chat_id=CHAT, user_id=A, sent_at=datetime.now(TZ_MSK))
    )
    svc = score_service_factory(conn)
    await svc.apply_reaction(B, CHAT, 1, "🔥")
    await svc.remove_reaction(B, CHAT, 1, "🔥")
    assert await journal(conn, A) == [
        entry(LedgerKind.REACTION, 3, 3, "msg:1", B),
        entry(LedgerKind.REACTION, -3, 0, "msg:1", B),
    ]


@pytest.mark.ledger_tampering
async def test_audit_catches_changes_bypassing_the_code(conn, app_container):
    await seed_users(conn, A, balance=10)

    # Ручной SQL всё равно попадает в журнал — но без типа операции
    await conn.execute("UPDATE scores SET value = value + 5 WHERE user_id = $1", A)
    result = await ledger_audit_loop.run_audit(app_container)
    assert (result.unknown_last_day, result.mismatches) == (1, [])

    # Журнал правили руками — баланс перестал с ним сходиться
    await conn.execute("DELETE FROM ledger WHERE user_id = $1 AND kind = 'unknown'", A)
    result = await ledger_audit_loop.run_audit(app_container)
    assert [(m.user_id, m.balance, m.ledger_total) for m in result.mismatches] == [(A, 15, 10)]
