"""E2E мута: мутить других и снимать мут могут только те, у кого есть право на муты."""

from __future__ import annotations

import pytest
from aiogram.methods import RestrictChatMember, SendMessage

import bot.presentation.handlers.poker as poker_module
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from tests.integration.fake_telegram import member
from tests.integration.helpers import BOT_ID, CHAT, balance, seed_users

ACTOR, VICTIM, IDLER = 701, 702, 703


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


def restricted(tg) -> list[int]:
    return [request.user_id for request in tg.requests(RestrictChatMember)]


def replies(tg) -> list[str]:
    return [request.text for request in tg.requests(SendMessage)]


async def muted_by(conn, user_id: int = VICTIM) -> int | None:
    return await conn.fetchval("SELECT muted_by FROM active_mutes WHERE user_id = $1 AND chat_id = $2", user_id, CHAT)


async def grant(tg, app_container, user_id: int, rights: str) -> None:
    """Кем участник приходится чату и боту."""
    match rights:
        case "restrict":  # админ с правом «Блокировка участников»
            tg.session.set_admin(user_id, can_restrict_members=True)
        case "owner":
            tg.session.member_status[user_id] = "creator"
        case "bot_admin":  # админ бота из конфига, в чате — обычный участник
            (await app_container.get(AppConfig)).admin.users = [str(user_id)]
        case "delete_only":  # модератор, который удаляет сообщения, но не ограничивает участников
            tg.session.set_admin(user_id)
        case "title":  # титул из /buyop или /op: админ без прав модерации
            tg.session.set_admin(user_id, can_delete_messages=False, can_pin_messages=False)


@pytest.mark.parametrize("rights", ["member", "title", "delete_only"])
async def test_mute_needs_mute_rights(tg, conn, app_container, texts, rights):
    await seed_users(conn, ACTOR, balance=100)
    await seed_users(conn, VICTIM, balance=5)
    await grant(tg, app_container, ACTOR, rights)

    # Раньше /mute продавался за баллы любому участнику
    for command in ("/mute @user702 5", "/amute @user702 5", "/mute random 5"):
        await tg.send(member(ACTOR), command)
    await tg.send(member(ACTOR), "/mute 5", reply_to=member(VICTIM))

    assert replies(tg) == [texts._t["mute_not_allowed"]] * 4
    assert restricted(tg) == []
    assert await muted_by(conn) is None
    assert (await balance(conn, ACTOR), await balance(conn, BOT_ID)) == (100, 0)


@pytest.mark.parametrize("rights", ["restrict", "owner", "bot_admin"])
async def test_mute_rights_holders_mute_for_free(tg, conn, app_container, texts, rights):
    await seed_users(conn, ACTOR, balance=100)
    await seed_users(conn, VICTIM)
    await grant(tg, app_container, ACTOR, rights)

    await tg.send(member(ACTOR), "/mute @user702 5")

    assert restricted(tg) == [VICTIM]
    assert await muted_by(conn) == ACTOR
    assert replies(tg) == [
        texts._t["mute_success"].format(actor="<code>@user701</code>", target="<code>@user702</code>", minutes=5)
    ]
    assert (await balance(conn, ACTOR), await balance(conn, BOT_ID)) == (100, 0)


async def test_mute_and_unmute_by_reply(tg, conn, app_container):
    await seed_users(conn, ACTOR, VICTIM)
    await grant(tg, app_container, ACTOR, "restrict")

    await tg.send(member(ACTOR), "/mute 5", reply_to=member(VICTIM))
    assert await muted_by(conn) == ACTOR

    await tg.send(member(ACTOR), "/unmute", reply_to=member(VICTIM))
    assert await muted_by(conn) is None
    assert restricted(tg) == [VICTIM, VICTIM]  # мут и снятие ограничений


async def test_unmute_needs_mute_rights(tg, conn, app_container, texts):
    await seed_users(conn, ACTOR, VICTIM)
    await seed_users(conn, IDLER, balance=1000)
    await grant(tg, app_container, ACTOR, "restrict")
    await tg.send(member(ACTOR), "/mute @user702 30")

    # Раньше мут снимал за баллы кто угодно — в том числе мут, выданный модератором
    await tg.send(member(IDLER), "/unmute @user702")
    await grant(tg, app_container, IDLER, "delete_only")
    await tg.send(member(IDLER), "/aunmute @user702")

    assert replies(tg)[1:] == [texts._t["mute_not_allowed"]] * 2
    assert await muted_by(conn) == ACTOR
    assert restricted(tg) == [VICTIM]
    assert await balance(conn, IDLER) == 1000

    await tg.send(member(ACTOR), "/aunmute @user702")
    assert await muted_by(conn) is None
    assert restricted(tg) == [VICTIM, VICTIM]


async def test_mute_random_picks_member_with_points(tg, conn, app_container, texts):
    await seed_users(conn, ACTOR)
    await seed_users(conn, VICTIM, balance=5)
    await seed_users(conn, IDLER, balance=0)  # без баллов — не кандидат
    await grant(tg, app_container, ACTOR, "restrict")

    await tg.send(member(ACTOR), "/mute random 5")

    assert restricted(tg) == [VICTIM]
    [reply] = replies(tg)
    assert reply.startswith(texts._t["mute_random_picked"].format(target="<code>@user702</code>"))


@pytest.mark.parametrize("victim_status", [None, "left", "kicked"], ids=["nobody", "left", "kicked"])
async def test_mute_random_without_candidates(tg, conn, app_container, texts, victim_status):
    await seed_users(conn, ACTOR)
    await grant(tg, app_container, ACTOR, "restrict")
    if victim_status is not None:
        # Баллы остались, но человек уже не в чате — мутить некого
        await seed_users(conn, VICTIM, balance=5)
        tg.session.member_status[VICTIM] = victim_status
    await tg.send(member(ACTOR), "/mute @random 5")

    assert replies(tg) == [texts._t["mute_random_no_candidates"]]
    assert restricted(tg) == []


async def test_chat_owner_cannot_be_muted(tg, conn, app_container, texts):
    await seed_users(conn, ACTOR, VICTIM)
    await grant(tg, app_container, ACTOR, "restrict")
    tg.session.member_status[VICTIM] = "creator"

    await tg.send(member(ACTOR), "/mute @user702 5")

    assert replies(tg) == [texts._t["mute_owner"]]
    assert restricted(tg) == []
    assert await muted_by(conn) is None
    assert not await (await app_container.get(RedisStore)).owner_mute_active(CHAT, VICTIM)


class _Draw:
    """random для кочерги: тянет мут, если он есть среди дебаффов; осечка — по флагу."""

    def __init__(self, backfire: bool) -> None:
        self._backfire = backfire

    def randint(self, low: int, high: int) -> int:
        return low if self._backfire else high

    def choice(self, debuffs: list[str]) -> str:
        return poker_module.DEBUFF_MUTE if poker_module.DEBUFF_MUTE in debuffs else debuffs[0]


@pytest.mark.parametrize(
    ("target", "backfire", "muted"),
    [(VICTIM, False, []), (None, False, [ACTOR]), (VICTIM, True, [ACTOR])],
    ids=["hit_other", "hit_self", "backfire"],
)
async def test_poker_mutes_only_the_hitter(tg, conn, monkeypatch, target, backfire, muted):
    await seed_users(conn, ACTOR, VICTIM, balance=1000)
    monkeypatch.setattr(poker_module, "random", _Draw(backfire))

    await tg.send(member(ACTOR), "/poker", reply_to=member(target) if target else None)

    # Раньше удар кочергой по другому мог его замутить
    assert restricted(tg) == muted
