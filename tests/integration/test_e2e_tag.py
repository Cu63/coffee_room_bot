"""E2E /tag: цена — по правам цели, в том числе у админа, которого бот сейчас держит в муте."""

from __future__ import annotations

import pytest
from aiogram.methods import SendMessage, SetChatMemberTag

from bot.application.score_service import ScoreService
from bot.domain.entities import Score
from bot.infrastructure.message_formatter import MessageFormatter
from tests.integration.fake_telegram import member
from tests.integration.helpers import BOT_ID, balance, seed_users

BUYER, TARGET, MOD = 761, 762, 763


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


def tags(tg) -> list[tuple[int, str | None]]:
    return [(request.user_id, request.tag) for request in tg.requests(SetChatMemberTag)]


async def mute_target(tg) -> None:
    """Модератор мутит цель: мут снимает с неё админку, для Telegram она теперь ограниченный участник."""
    tg.session.set_admin(MOD, can_restrict_members=True)
    await tg.send(member(MOD), f"/mute @user{TARGET} 30")
    assert tg.session.member_status[TARGET] == "restricted"


def grant(tg, rights: str) -> None:
    match rights:
        case "delete_and_ban":  # ADMIN_RIGHTS уже умеет удалять сообщения
            tg.session.set_admin(TARGET, can_restrict_members=True)
        case "delete_only":
            tg.session.set_admin(TARGET)
        case "title":  # титул из /buyop: админ без прав модерации
            tg.session.set_admin(TARGET, can_delete_messages=False, can_pin_messages=False)
        case "owner":
            tg.session.member_status[TARGET] = "creator"


@pytest.mark.parametrize(
    ("rights", "muted", "price"),
    [
        ("delete_and_ban", True, "cost_admin"),
        ("delete_and_ban", False, "cost_admin"),
        ("delete_only", True, "cost_member"),
        ("title", True, "cost_member"),
        ("member", False, "cost_member"),
        ("owner", False, "cost_owner"),
    ],
)
async def test_tag_price_follows_delete_and_ban_rights_even_in_mute(tg, conn, config, rights, muted, price):
    await seed_users(conn, BUYER, balance=1000)
    await seed_users(conn, TARGET, MOD)
    grant(tg, rights)
    if muted:
        await mute_target(tg)

    await tg.send(member(BUYER), f"/tag @user{TARGET} кофеман")

    # Раньше мут снимал с админа админку, и тег ему стоил как обычному участнику
    cost = getattr(config.tag, price)
    assert (await balance(conn, BUYER), await balance(conn, BOT_ID)) == (1000 - cost, cost)
    assert tags(tg) == [(TARGET, "кофеман")]


@pytest.mark.parametrize(("arg", "title"), [("кофеман", "кофеман"), ("--clear", None)])
async def test_tag_bought_during_mute_stays_after_unmute(tg, conn, arg, title):
    await seed_users(conn, BUYER, balance=1000)
    await seed_users(conn, TARGET, MOD)
    grant(tg, "delete_and_ban")
    await mute_target(tg)
    await tg.send(member(BUYER), f"/tag @user{TARGET} {arg}")

    await tg.send(member(MOD), f"/unmute @user{TARGET}")

    # Раньше размут возвращал титул из снимка до мута, и купленный тег пропадал
    assert tg.session.member_status[TARGET] == "administrator"
    assert tg.session.admin_rights[TARGET].get("custom_title") == title


async def test_tag_is_not_changed_without_payment(tg, conn, monkeypatch):
    """Баланс проверили, а к оплате денег уже нет (их потратили параллельно) — тег не меняется."""
    await seed_users(conn, BUYER, balance=0)
    await seed_users(conn, TARGET)

    async def looks_rich(self, user_id, chat_id):
        return Score(user_id=user_id, chat_id=chat_id, value=10_000)

    monkeypatch.setattr(ScoreService, "get_score", looks_rich)
    await tg.send(member(BUYER), f"/tag @user{TARGET} кофеман")

    # Раньше тег ставился до оплаты и оставался, даже если списать не вышло
    assert tags(tg) == []
    assert await balance(conn, BUYER) == 0


async def test_rejected_tag_charges_nothing(tg, conn, config, texts):
    await seed_users(conn, BUYER, balance=1000)
    await seed_users(conn, TARGET)
    tg.session.errors[SetChatMemberTag] = "Bad Request: not enough rights"

    await tg.send(member(BUYER), f"/tag @user{TARGET} кофеман")
    assert tg.requests(SendMessage)[-1].text == texts._t["tag_failed"]
    assert (await balance(conn, BUYER), await balance(conn, BOT_ID)) == (1000, 0)

    # Неудачная попытка не ставит кулдаун
    del tg.session.errors[SetChatMemberTag]
    await tg.send(member(BUYER), f"/tag @user{TARGET} кофеман")
    assert await balance(conn, BUYER) == 1000 - config.tag.cost_member
