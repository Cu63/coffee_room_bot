"""E2E штрафов за мут: платит замученный, касса бота держит штраф при муте, /unmute его возвращает.

Штраф берут за /mute модератора и за автомут по предупреждениям, но не за /mute random. Мут, который
истёк сам, штраф не возвращает; из двух одновременных /unmute его возвращает один.
"""

from __future__ import annotations

import asyncio

import pytest
from aiogram.methods import RestrictChatMember, SendMessage

import bot.infrastructure.unmute_loop as unmute_loop
from bot.application.mute_service import MuteService
from bot.infrastructure.message_formatter import MessageFormatter
from tests.integration.fake_telegram import member
from tests.integration.helpers import BOT_ID, balance, seed_users

MOD, MOD2, VICTIM = 721, 722, 723
VICTIM_LINK = f"<code>@user{VICTIM}</code>"
START = 500  # на счету у замученного


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def chat(tg, conn) -> None:
    await seed_users(conn, MOD, MOD2)
    await seed_users(conn, VICTIM, balance=START)
    await seed_users(conn, BOT_ID, balance=0, is_bot=True)  # касса бота
    tg.session.set_admin(MOD, can_restrict_members=True)
    tg.session.set_admin(MOD2, can_restrict_members=True)


def replies(tg) -> list[str]:
    return [request.text for request in tg.requests(SendMessage)]


async def money(conn) -> tuple[int, int]:
    """Счёт замученного и касса бота."""
    return await balance(conn, VICTIM), await balance(conn, BOT_ID)


async def ledger(conn) -> list[tuple[str, str, int]]:
    rows = await conn.fetch(
        "SELECT kind, ref, delta FROM ledger WHERE user_id = $1 AND kind <> 'opening' ORDER BY id", VICTIM
    )
    return [(row["kind"], row["ref"], row["delta"]) for row in rows]


async def test_mute_fines_and_unmute_refunds(tg, conn, texts, config, chat):
    fine = config.mute.fine
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")

    assert replies(tg)[-1].endswith("\n" + texts._t["mute_fine"].format(amount=texts.amount(fine)))
    # Платит замученный, модератору бесплатно
    assert await money(conn) == (START - fine, fine)
    assert await balance(conn, MOD) == 0

    await tg.send(member(MOD), f"/unmute @user{VICTIM}")

    assert replies(tg)[-1] == (
        texts._t["unmute_success"].format(user=VICTIM_LINK)
        + "\n"
        + texts._t["unmute_refund"].format(amount=texts.amount(fine))
    )
    assert await money(conn) == (START, 0)
    assert await ledger(conn) == [("penalty", "mute", -fine), ("refund", "unmute", fine)]


async def test_stacked_mutes_are_refunded_together(tg, conn, config, chat):
    fine = config.mute.fine
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    await tg.send(member(MOD2), f"/mute @user{VICTIM} 30")
    assert await money(conn) == (START - 2 * fine, 2 * fine)

    await tg.send(member(MOD), f"/unmute @user{VICTIM}")

    assert await money(conn) == (START, 0)


async def test_fine_takes_only_what_there_is(tg, conn, texts, config, chat):
    short = config.mute.fine // 2
    await seed_users(conn, VICTIM, balance=short)

    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    assert replies(tg)[-1].endswith(texts._t["mute_fine"].format(amount=texts.amount(short)))
    assert await money(conn) == (0, short)

    await tg.send(member(MOD), f"/unmute @user{VICTIM}")
    assert await money(conn) == (short, 0)


async def test_expired_mute_keeps_the_fine(tg, conn, app_container, texts, config, chat):
    fine = config.mute.fine
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    await conn.execute("UPDATE active_mutes SET until_at = now() - interval '1 second'")

    await unmute_loop._tick(tg.bot, app_container)
    await tg.send(member(MOD), f"/unmute @user{VICTIM}")  # снимать уже нечего — и возвращать тоже

    assert replies(tg)[-1] == texts._t["unmute_not_muted"].format(user=VICTIM_LINK)
    assert await money(conn) == (START - fine, fine)


async def test_warn_limit_fines_with_the_mute(tg, conn, texts, config, chat):
    fine = config.warn.fine
    for _ in range(config.warn.limit):
        await tg.send(member(MOD), f"/warn @user{VICTIM}")

    assert replies(tg)[-1].endswith(
        texts._t["warn_muted"].format(minutes=config.warn.mute_minutes)
        + "\n"
        + texts._t["mute_fine"].format(amount=texts.amount(fine))
    )
    assert await money(conn) == (START - fine, fine)

    # Штраф лежит при автомуте, как при /mute: /unmute возвращает и его
    await tg.send(member(MOD), f"/unmute @user{VICTIM}")
    assert await money(conn) == (START, 0)
    assert await ledger(conn) == [("penalty", "warn", -fine), ("refund", "unmute", fine)]


async def test_random_mute_is_not_fined(tg, conn, chat):
    await tg.send(member(MOD), "/mute random 5")

    assert [request.user_id for request in tg.requests(RestrictChatMember)] == [VICTIM]
    assert await money(conn) == (START, 0)


async def test_no_mute_no_fine(tg, conn, texts, config, chat):
    tg.session.member_status[VICTIM] = "creator"  # владельца Telegram не ограничивает

    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    for _ in range(config.warn.limit):
        await tg.send(member(MOD), f"/warn @user{VICTIM}")

    assert replies(tg)[0] == texts._t["mute_owner"]
    assert replies(tg)[-1].endswith(texts._t["warn_mute_impossible"])
    assert await money(conn) == (START, 0)


async def test_refund_takes_what_the_house_has(tg, conn, texts, config, chat):
    fine = config.mute.fine
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    left = fine // 4
    await seed_users(conn, BOT_ID, balance=left, is_bot=True)  # касса успела раздать призы

    await tg.send(member(MOD), f"/unmute @user{VICTIM}")

    assert replies(tg)[-1].endswith(
        texts._t["unmute_refund_partial"].format(amount=texts.amount(left), fine=texts.amount(fine))
    )
    assert await money(conn) == (START - fine + left, 0)


async def test_simultaneous_unmutes_refund_once(tg, conn, monkeypatch, config, chat):
    house = 1000  # в пустой кассе второй возврат упёрся бы в ноль и двойной возврат был бы не виден
    await seed_users(conn, BOT_ID, balance=house, is_bot=True)
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")

    # Оба модератора сняли ограничения и удаляют запись о муте одновременно: штраф вернёт тот,
    # чьё удаление прошло. Прочитай штраф до удаления — вернули бы оба
    both_here = asyncio.Barrier(2)
    original_delete = MuteService.delete_mute

    async def delete_together(self, *args):
        await asyncio.wait_for(both_here.wait(), 5)
        return await original_delete(self, *args)

    monkeypatch.setattr(MuteService, "delete_mute", delete_together)
    await asyncio.gather(
        tg.send(member(MOD), f"/unmute @user{VICTIM}"), tg.send(member(MOD2), f"/unmute @user{VICTIM}")
    )

    assert await money(conn) == (START, house)


async def test_served_mute_keeps_its_fine_until_the_loop_lifts_it(tg, conn, config, chat):
    """Мут истёк, а фоновая задача ещё не сняла запись: штраф за отбытый мут всё равно остаётся кассе."""
    fine = config.mute.fine
    expire = "UPDATE active_mutes SET until_at = now() - interval '1 second'"

    # Новый мут поверх отбытого: /unmute вернёт только новый штраф
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    await conn.execute(expire)
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    await tg.send(member(MOD), f"/unmute @user{VICTIM}")
    assert await money(conn) == (START - fine, fine)

    # /unmute отбытого мута штраф не возвращает
    await tg.send(member(MOD), f"/mute @user{VICTIM} 30")
    await conn.execute(expire)
    await tg.send(member(MOD), f"/unmute @user{VICTIM}")
    assert await money(conn) == (START - 2 * fine, 2 * fine)
