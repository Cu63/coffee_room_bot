"""E2E: платный мут — оплата в кассу бота, /mute random, /unmute."""

from __future__ import annotations

import pytest
from aiogram.methods import RestrictChatMember, SendMessage

from bot.infrastructure.message_formatter import MessageFormatter
from tests.integration.fake_telegram import member
from tests.integration.helpers import BOT_ID, balance, seed_users

ACTOR, VICTIM, IDLER = 701, 702, 703


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
def cost_per_minute(config) -> int:
    return config.mute.cost_per_minute


def restricted(tg) -> list[int]:
    return [request.user_id for request in tg.requests(RestrictChatMember)]


async def test_paid_mute_goes_to_bot(tg, conn, cost_per_minute):
    await seed_users(conn, ACTOR, balance=100)
    await seed_users(conn, VICTIM)
    await tg.send(member(ACTOR), "/mute @user702 5")

    cost = 5 * cost_per_minute
    assert (await balance(conn, ACTOR), await balance(conn, BOT_ID)) == (100 - cost, cost)
    assert restricted(tg) == [VICTIM]


async def test_mute_random_picks_member_with_points(tg, conn, texts, cost_per_minute):
    await seed_users(conn, ACTOR, balance=100)
    await seed_users(conn, VICTIM, balance=5)
    await seed_users(conn, IDLER, balance=0)  # без баллов — не кандидат
    await tg.send(member(ACTOR), "/mute random 5")

    assert restricted(tg) == [VICTIM]
    cost = 5 * cost_per_minute
    assert (await balance(conn, ACTOR), await balance(conn, BOT_ID)) == (100 - cost, cost)
    [reply] = tg.requests(SendMessage)
    assert reply.text.startswith(texts._t["mute_random_picked"].format(target="<code>@user702</code>"))


@pytest.mark.parametrize("victim_status", [None, "left", "kicked"], ids=["nobody", "left", "kicked"])
async def test_mute_random_without_candidates_charges_nothing(tg, conn, texts, victim_status):
    await seed_users(conn, ACTOR, balance=100)
    if victim_status is not None:
        # Баллы остались, но человек уже не в чате — мутить некого
        await seed_users(conn, VICTIM, balance=5)
        tg.session.member_status[VICTIM] = victim_status
    await tg.send(member(ACTOR), "/mute @random 5")

    [reply] = tg.requests(SendMessage)
    assert reply.text == texts._t["mute_random_no_candidates"]
    assert restricted(tg) == []
    assert (await balance(conn, ACTOR), await balance(conn, BOT_ID)) == (100, 0)


async def test_paid_unmute_goes_to_bot(tg, conn):
    await seed_users(conn, ACTOR, IDLER, balance=100)
    await seed_users(conn, VICTIM)
    await tg.send(member(ACTOR), "/mute @user702 5")
    paid_for_mute = await balance(conn, BOT_ID)

    await tg.send(member(IDLER), "/unmute @user702")

    unmute_cost = 100 - await balance(conn, IDLER)
    assert unmute_cost > 0
    assert await balance(conn, BOT_ID) == paid_for_mute + unmute_cost
    assert restricted(tg) == [VICTIM, VICTIM]  # мут и снятие ограничений
