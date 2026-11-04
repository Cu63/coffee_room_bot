"""E2E: ограничение, оставшееся от старого размута (можно всё, кроме смены своего тега), снимается.

Раньше размут не передавал can_edit_tag, а непереданное право Telegram считает запретом: участник
оставался ограниченным без срока, и бот принимал это за ручное ограничение админа.
"""

from __future__ import annotations

import pytest
from aiogram.methods import SendMessage
from aiogram.types import ChatPermissions

from bot.infrastructure.message_formatter import MessageFormatter
from tests.integration.fake_telegram import member
from tests.integration.helpers import CHAT, seed_users

MOD, STUCK, MANUAL, MUTED, FREE = 771, 772, 773, 774, 775

# Так выглядит участник после старого размута: разрешено всё, кроме can_edit_tag
LEFTOVER = {field: field != "can_edit_tag" for field in ChatPermissions.model_fields}


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


def restrict(tg, user_id: int, **perms: bool) -> None:
    tg.session.member_status[user_id] = "restricted"
    tg.session.restrictions[user_id] = LEFTOVER | perms


def replies(tg) -> list[str]:
    return [request.text for request in tg.requests(SendMessage)]


async def muted(conn, user_id: int) -> bool:
    return bool(await conn.fetchval("SELECT count(*) FROM active_mutes WHERE user_id = $1", user_id))


async def test_leftover_restriction_does_not_block_a_mute(tg, conn):
    await seed_users(conn, MOD, STUCK)
    tg.session.set_admin(MOD, can_restrict_members=True)
    restrict(tg, STUCK)

    await tg.send(member(MOD), f"/mute @user{STUCK} 5")

    assert await muted(conn, STUCK)
    assert not tg.session.restrictions[STUCK]["can_send_messages"]


async def test_unmute_lifts_a_leftover_restriction(tg, conn, texts):
    await seed_users(conn, MOD, STUCK)
    tg.session.set_admin(MOD, can_restrict_members=True)
    restrict(tg, STUCK)

    await tg.send(member(MOD), f"/unmute @user{STUCK}")

    assert tg.session.member_status[STUCK] == "member"
    assert replies(tg) == [texts._t["unmute_success"].format(user=f"<code>@user{STUCK}</code>")]


async def test_manual_restriction_stays(tg, conn, texts):
    await seed_users(conn, MOD, MANUAL)
    tg.session.set_admin(MOD, can_restrict_members=True)
    restrict(tg, MANUAL, can_send_photos=False)  # админ вручную запретил фото

    await tg.send(member(MOD), f"/unmute @user{MANUAL}")
    await tg.send(member(MOD), f"/mute @user{MANUAL} 5")

    assert tg.session.member_status[MANUAL] == "restricted"
    assert not tg.session.restrictions[MANUAL]["can_send_photos"]
    assert not await muted(conn, MANUAL)
    assert replies(tg)[0] == texts._t["unmute_not_muted"].format(user=f"<code>@user{MANUAL}</code>")


async def test_unstuck_frees_only_members_left_restricted_by_old_unmutes(tg, conn, texts):
    await seed_users(conn, MOD, STUCK, MANUAL, MUTED, FREE)
    tg.session.set_admin(MOD, can_restrict_members=True)
    for user_id in (STUCK, MANUAL, FREE):  # всех их бот когда-то мутил
        await conn.execute(
            "INSERT INTO mute_history (chat_id, user_id, muted_by) VALUES ($1, $2, $3)", CHAT, user_id, MOD
        )
    restrict(tg, STUCK)
    restrict(tg, MANUAL, can_send_photos=False)
    await tg.send(member(MOD), f"/mute @user{MUTED} 30")  # сейчас в муте — не трогаем

    await tg.send(member(MOD), "/unstuck")

    assert tg.session.member_status[STUCK] == "member"
    assert tg.session.member_status[MANUAL] == "restricted"
    assert tg.session.member_status[MUTED] == "restricted" and await muted(conn, MUTED)
    assert replies(tg)[-1] == texts._t["unstuck_done"].format(count=1, users=f"<code>@user{STUCK}</code>")

    await tg.send(member(MOD), "/unstuck")
    assert replies(tg)[-1] == texts._t["unstuck_none"]


async def test_unstuck_needs_mute_rights(tg, conn, texts):
    await seed_users(conn, MOD, STUCK, FREE)
    await conn.execute("INSERT INTO mute_history (chat_id, user_id, muted_by) VALUES ($1, $2, $3)", CHAT, STUCK, MOD)
    restrict(tg, STUCK)

    await tg.send(member(FREE), "/unstuck")

    assert replies(tg) == [texts._t["mute_not_allowed"]]
    assert tg.session.member_status[STUCK] == "restricted"
