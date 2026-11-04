"""E2E предупреждений и /mutelist: выдают те, у кого есть право на муты; лимит за срок — автомут, один."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from aiogram.methods import RestrictChatMember, SendMessage

from bot.application.warn_service import WarnService
from bot.infrastructure.message_formatter import MessageFormatter
from tests.integration.fake_telegram import member
from tests.integration.helpers import CHAT, seed_users

MOD, MOD2, TARGET, MEMBER = 781, 782, 783, 784
TARGET_LINK = f"<code>@user{TARGET}</code>"


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def chat(tg, conn) -> None:
    await seed_users(conn, MOD, MOD2, TARGET, MEMBER)
    tg.session.set_admin(MOD, can_restrict_members=True)
    tg.session.set_admin(MOD2, can_restrict_members=True)


def replies(tg) -> list[str]:
    return [request.text for request in tg.requests(SendMessage)]


def muted(tg) -> list[int]:
    return [request.user_id for request in tg.requests(RestrictChatMember)]


async def warnings(conn) -> list[str]:
    rows = await conn.fetch("SELECT reason FROM warnings WHERE user_id = $1 ORDER BY id", TARGET)
    return [row["reason"] for row in rows]


async def test_only_mute_rights_holders_warn(tg, conn, texts, chat):
    for command in (f"/warn @user{TARGET} флуд", f"/unwarn @user{TARGET}", "/mutelist"):
        await tg.send(member(MEMBER), command)

    assert replies(tg) == [texts._t["mute_not_allowed"]] * 3
    assert await warnings(conn) == []


async def test_warnings_add_up_to_a_mute(tg, conn, texts, config, chat):
    limit = config.warn.limit
    for n in range(1, limit):
        await tg.send(member(MOD), f"/warn @user{TARGET} флуд <b>капсом</b>")
        assert replies(tg)[-1] == texts._t["warn_issued"].format(target=TARGET_LINK, count=n, limit=limit) + texts._t[
            "warn_reason"
        ].format(reason="флуд &lt;b&gt;капсом&lt;/b&gt;")
    assert muted(tg) == []

    await tg.send(member(MOD), f"/warn @user{TARGET}")

    # Лимит — автомут, и предупреждения сгорают
    assert muted(tg) == [TARGET]
    assert texts._t["warn_muted"].format(minutes=config.warn.mute_minutes) in replies(tg)[-1]
    assert await warnings(conn) == []
    until = await conn.fetchval("SELECT until_at FROM active_mutes WHERE user_id = $1", TARGET)
    left = until - datetime.now(until.tzinfo)
    assert abs(left - timedelta(minutes=config.warn.mute_minutes)) < timedelta(minutes=1)


async def test_old_warnings_do_not_count(tg, conn, texts, config, chat):
    for _ in range(config.warn.limit - 1):
        await conn.execute(
            "INSERT INTO warnings (chat_id, user_id, issued_by, created_at) VALUES ($1, $2, $3, now() - $4::interval)",
            CHAT,
            TARGET,
            MOD,
            timedelta(days=config.warn.period_days + 1),
        )

    await tg.send(member(MOD), f"/warn @user{TARGET}")

    assert replies(tg)[-1] == texts._t["warn_issued"].format(target=TARGET_LINK, count=1, limit=config.warn.limit)
    assert muted(tg) == []


async def test_warns_lists_and_unwarn_removes_the_latest(tg, conn, texts, config, chat):
    await tg.send(member(MOD), f"/warn @user{TARGET} первое")
    await tg.send(member(MOD2), f"/warn @user{TARGET} второе")

    await tg.send(member(MEMBER), f"/warns @user{TARGET}")  # смотреть может любой
    listing = replies(tg)[-1]
    assert listing.startswith(
        texts._t["warns_header"].format(
            target=TARGET_LINK, count=2, limit=config.warn.limit, days=config.warn.period_days
        )
    )
    assert listing.index("второе") < listing.index("первое")  # новые сверху

    await tg.send(member(MOD), f"/unwarn @user{TARGET}")
    assert replies(tg)[-1] == texts._t["unwarn_done"].format(target=TARGET_LINK, count=1, limit=config.warn.limit)
    assert await warnings(conn) == ["первое"]


async def test_simultaneous_warns_at_the_limit_mute_once(tg, conn, monkeypatch, texts, config, chat):
    for _ in range(config.warn.limit - 1):
        await tg.send(member(MOD), f"/warn @user{TARGET}")

    # Оба модератора выдают предупреждение одновременно. Без блокировки оба насчитали бы лимит:
    # второй сообщил бы «лимит набран, а замутить нельзя» (мут первого он ещё не видит)
    both_here = asyncio.Barrier(2)
    original_warn = WarnService.warn

    async def warn_together(self, *args):
        await asyncio.wait_for(both_here.wait(), 5)
        return await original_warn(self, *args)

    monkeypatch.setattr(WarnService, "warn", warn_together)
    await asyncio.gather(
        tg.send(member(MOD), f"/warn @user{TARGET} a"), tg.send(member(MOD2), f"/warn @user{TARGET} b")
    )

    assert muted(tg) == [TARGET]
    first, second = sorted(replies(tg)[-2:], key=lambda text: texts._t["warn_muted"].split("{")[0] in text)
    assert second.endswith(texts._t["warn_muted"].format(minutes=config.warn.mute_minutes))
    # Второе предупреждение выдано уже после мута — первое из новой серии
    issued = texts._t["warn_issued"].format(target=TARGET_LINK, count=1, limit=config.warn.limit)
    assert first in {issued + texts._t["warn_reason"].format(reason=reason) for reason in ("a", "b")}
    assert len(await warnings(conn)) == 1


async def test_mutelist_shows_who_is_muted_and_by_whom(tg, conn, texts, chat):
    await tg.send(member(MOD), "/mutelist")
    assert replies(tg)[-1] == texts._t["mutelist_none"]

    await tg.send(member(MOD), f"/mute @user{TARGET} 30")
    await tg.send(member(MEMBER), "/selfmute 10")
    await tg.send(member(MOD), "/mutelist")

    lines = replies(tg)[-1].split("\n")
    assert lines[0] == texts._t["mutelist_header"].format(count=2)
    # Кто выйдет раньше — выше
    assert lines[1].startswith(f"<code>@user{MEMBER}</code> — ещё ") and lines[1].endswith(texts._t["mutelist_self"])
    assert lines[2].startswith(f"{TARGET_LINK} — ещё ") and lines[2].endswith(f"<code>@user{MOD}</code>")
