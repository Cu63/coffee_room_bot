"""E2E модерации.

Мут не теряет админку, не трогает ручные ограничения и не укорачивает более длинный мут;
режимы чата не списывают дважды и не теряют права чата; титул из /buyop — не модератор;
админы бота — по id.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

import pytest
from aiogram.methods import PromoteChatMember, RestrictChatMember, SendMessage

from bot.application.mute_service import MuteService
from bot.domain.tz import TZ_MSK
from bot.infrastructure.bootstrap import warn_about_username_admins
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.db.postgres_chatmode_repository import PostgresChatmodeRepository
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.handlers._admin_utils import MuteOutcome, apply_mute
from tests.integration.fake_telegram import CHAT_PERMISSIONS, member
from tests.integration.helpers import BOT_ID, CHAT, balance, seed_users

ACTOR, VICTIM, HELPER, MODERATOR = 711, 712, 713, 714


async def mute_row(conn) -> dict | None:
    row = await conn.fetchrow(
        "SELECT until_at, was_admin, admin_permissions FROM active_mutes WHERE user_id = $1 AND chat_id = $2",
        VICTIM,
        CHAT,
    )
    if row is None:
        return None
    perms = row["admin_permissions"]
    return {"until_at": row["until_at"], "was_admin": row["was_admin"], "perms": json.loads(perms) if perms else None}


def grant_mute_rights(tg, user_id: int = ACTOR) -> None:
    tg.session.set_admin(user_id, can_restrict_members=True)


async def test_second_mute_keeps_admin_rights_and_unmute_restores_them(tg, conn):
    await seed_users(conn, ACTOR, VICTIM)
    grant_mute_rights(tg)
    tg.session.member_status[VICTIM] = "administrator"

    await tg.send(member(ACTOR), "/mute @user712 1")
    first = await mute_row(conn)
    assert first["was_admin"] and first["perms"]["can_delete_messages"] and first["perms"]["custom_title"] == "модер"

    # После мута Telegram видит его уже ограниченным участником, без админки.
    # Раньше второй мут перезаписывал снимок на was_admin=False — админка терялась навсегда
    tg.session.member_status[VICTIM] = "restricted"
    await tg.send(member(ACTOR), "/mute @user712 1")
    second = await mute_row(conn)
    assert (second["was_admin"], second["perms"]) == (True, first["perms"])
    assert second["until_at"] > first["until_at"]  # время стекуется

    await tg.send(member(ACTOR), "/unmute @user712")
    assert await mute_row(conn) is None
    restored = [p for p in tg.requests(PromoteChatMember) if p.user_id == VICTIM][-1]
    assert restored.can_delete_messages and restored.can_pin_messages


async def test_mute_does_not_touch_restriction_set_by_admin(tg, conn, app_container):
    await seed_users(conn, ACTOR, VICTIM)
    grant_mute_rights(tg)
    tg.session.member_status[VICTIM] = "restricted"  # админ ограничил вручную, бот об этом не знает

    await tg.send(member(ACTOR), "/mute @user712 1")

    # Раньше мут ботом заменял ручное ограничение, а размут снимал его совсем
    texts = await app_container.get(MessageFormatter)
    [reply] = tg.requests(SendMessage)
    assert reply.text == texts._t["mute_restricted_by_admin"].format(target="<code>@user712</code>")
    assert tg.requests(RestrictChatMember) == []
    assert await mute_row(conn) is None


async def test_roulette_or_poker_mute_does_not_shorten_longer_mute(tg, conn, app_container):
    await seed_users(conn, ACTOR, VICTIM)
    grant_mute_rights(tg)
    await tg.send(member(ACTOR), "/mute @user712 30")
    longer = await mute_row(conn)
    tg.session.member_status[VICTIM] = "restricted"

    # Рулетка или кочерга мутят на 5 минут — выданные модератором 30 не должны сократиться
    async with app_container() as scope:
        mute_service = await scope.get(MuteService)
        result = await apply_mute(
            tg.bot,
            mute_service,
            target_id=VICTIM,
            chat_id=CHAT,
            muted_by=BOT_ID,
            until=datetime.now(TZ_MSK) + timedelta(minutes=5),
        )

    assert (result.outcome, result.until) == (MuteOutcome.MUTED, longer["until_at"])
    assert (await mute_row(conn))["until_at"] == longer["until_at"]
    assert tg.requests(RestrictChatMember)[-1].until_date == longer["until_at"]


# ── /silence и /gif ───────────────────────────────────────────────────────────


@pytest.fixture
async def silence_cost(app_container) -> int:
    config = await app_container.get(AppConfig)
    return config.chatmode.silence.cost_per_minute * 5


async def chatmode_row(conn) -> dict | None:
    row = await conn.fetchrow("SELECT activated_by, saved_perms FROM chatmode WHERE chat_id = $1", CHAT)
    return None if row is None else {"activated_by": row["activated_by"], "saved_perms": json.loads(row["saved_perms"])}


async def test_parallel_silence_charges_once_and_keeps_chat_permissions(tg, conn, monkeypatch, silence_cost):
    await seed_users(conn, ACTOR, HELPER, balance=1000)

    # Оба /silence видят, что режима нет, и только потом идут дальше.
    # Раньше оба платили, а второй перезаписывал сохранённые права чата правами «тишины»
    both_checked = asyncio.Barrier(2)
    calls = 0
    original_get = PostgresChatmodeRepository.get

    async def get_after_both_checked(self, chat_id):
        nonlocal calls
        entry = await original_get(self, chat_id)
        calls += 1
        if calls <= 2:
            await asyncio.wait_for(both_checked.wait(), 5)
        return entry

    monkeypatch.setattr(PostgresChatmodeRepository, "get", get_after_both_checked)
    await asyncio.gather(tg.send(member(ACTOR), "/silence 5"), tg.send(member(HELPER), "/silence 5"))

    mode = await chatmode_row(conn)
    payer = mode["activated_by"]
    assert sorted([await balance(conn, ACTOR), await balance(conn, HELPER)]) == [1000 - silence_cost, 1000]
    assert (await balance(conn, payer), await balance(conn, BOT_ID)) == (1000 - silence_cost, silence_cost)
    assert mode["saved_perms"] == CHAT_PERMISSIONS
    assert not tg.session.chat_permissions["can_send_messages"]

    await tg.send(member(payer), "/chatmode off")
    assert await chatmode_row(conn) is None
    assert tg.session.chat_permissions == CHAT_PERMISSIONS


async def test_silence_without_money_leaves_chat_free(tg, conn, silence_cost):
    await seed_users(conn, ACTOR)
    await seed_users(conn, HELPER, balance=1000)

    await tg.send(member(ACTOR), "/silence 5")
    assert await chatmode_row(conn) is None  # чат занимается до оплаты — без денег бронь снимается
    assert tg.session.chat_permissions == CHAT_PERMISSIONS

    await tg.send(member(HELPER), "/silence 5")
    assert (await chatmode_row(conn))["activated_by"] == HELPER
    assert await balance(conn, HELPER) == 1000 - silence_cost


@pytest.mark.parametrize("mode", ["silence", "gif"])
async def test_chat_mode_is_limited_to_max_minutes(tg, conn, app_container, mode):
    preset = getattr((await app_container.get(AppConfig)).chatmode, mode)
    await seed_users(conn, ACTOR, balance=1000)

    await tg.send(member(ACTOR), f"/{mode} {preset.max_minutes + 1}")
    assert tg.requests(SendMessage)[-1].text == f"❌ Максимальная длительность — {preset.max_minutes} мин."
    assert await chatmode_row(conn) is None
    assert await balance(conn, ACTOR) == 1000

    await tg.send(member(ACTOR), f"/{mode} {preset.max_minutes}")
    assert await chatmode_row(conn) is not None
    assert await balance(conn, ACTOR) == 1000 - preset.cost_per_minute * preset.max_minutes


# ── Титул из /buyop — не модератор ────────────────────────────────────────────


async def buy_op(tg, conn, user_id: int) -> None:
    await seed_users(conn, user_id, balance=5000)
    await tg.send(member(user_id), "/buyop")
    assert tg.session.member_status[user_id] == "administrator"  # в Telegram он теперь админ — с титулом


async def test_bought_admin_cannot_cancel_paid_silence(tg, conn):
    await buy_op(tg, conn, HELPER)
    await seed_users(conn, ACTOR, balance=1000)
    await tg.send(member(ACTOR), "/silence 5")

    await tg.send(member(HELPER), "/chatmode off")
    assert tg.requests(SendMessage)[-1].text == "❌ Отменить режим может только его инициатор или администратор."
    assert await chatmode_row(conn) is not None
    assert not tg.session.chat_permissions["can_send_messages"]

    tg.session.member_status[MODERATOR] = "administrator"  # настоящий модератор: может удалять сообщения
    await tg.send(member(MODERATOR), "/chatmode off")
    assert await chatmode_row(conn) is None
    assert tg.session.chat_permissions == CHAT_PERMISSIONS


async def test_bought_admin_cannot_configure_tracker(tg, conn):
    await buy_op(tg, conn, HELPER)

    await tg.send(member(HELPER), "/settracker -1009999")
    assert tg.requests(SendMessage)[-1].text == "❌ Только администраторы могут настраивать трекер."

    tg.session.member_status[MODERATOR] = "administrator"
    await tg.send(member(MODERATOR), "/settracker -1009999")
    assert tg.requests(SendMessage)[-1].text == "✅ Трекер привязан: <code>-1009999</code>"


# ── Админы бота по id ─────────────────────────────────────────────────────────


async def test_admin_listed_by_id_keeps_rights_after_rename(tg, conn, app_container):
    config = await app_container.get(AppConfig)
    config.admin.users = [str(ACTOR)]
    await seed_users(conn, VICTIM)

    await tg.send(member(ACTOR, username="new_name"), "/add @user712 10")
    assert await balance(conn, VICTIM) == 10

    texts = await app_container.get(MessageFormatter)
    await tg.send(member(HELPER, username="user711"), "/add @user712 10")  # занял старое имя админа
    assert tg.requests(SendMessage)[-1].text == texts._t["admin_not_allowed"]
    assert await balance(conn, VICTIM) == 10


async def test_username_admins_get_a_hint_with_their_id(app_container, conn, caplog):
    await seed_users(conn, ACTOR)

    with caplog.at_level(logging.WARNING, logger="bot.infrastructure.bootstrap"):
        await warn_about_username_admins(app_container, [str(HELPER), "user711", "ghost"])

    hints = [record.getMessage() for record in caplog.records]
    assert len(hints) == 2
    assert "@user711" in hints[0] and f"id: {ACTOR}" in hints[0]
    assert "@ghost" in hints[1]
