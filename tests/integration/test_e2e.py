"""E2E: апдейт проходит через весь бот — middleware, роутеры, DI, транзакцию — до запросов в Bot API."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, PinChatMessage, SendMessage, UnpinChatMessage

from bot.infrastructure import lot_loop
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from tests.integration.fake_telegram import member
from tests.integration.helpers import BOT_ID, CHAT, balance, seed_users

# ── /transfer ─────────────────────────────────────────────────────────

ALICE, BOB = 501, 502


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def accounts(conn):
    await seed_users(conn, ALICE, balance=100)
    await seed_users(conn, BOB, balance=0)


async def test_transfer(tg, conn, texts, accounts):
    command_id = await tg.send(member(ALICE), "/transfer @user502 30")

    assert (await balance(conn, ALICE), await balance(conn, BOB)) == (70, 30)
    [reply] = tg.requests(SendMessage)
    p = texts._p
    assert reply.text == texts._t["transfer_success"].format(
        sender="<code>@user501</code>",
        receiver="<code>@user502</code>",
        amount=30,
        score_word=p.pluralize(30),
        sender_balance=70,
        score_word_sender=p.pluralize(70),
    )
    assert reply.parse_mode == "HTML"
    assert reply.reply_parameters.message_id == command_id


async def test_transfer_escapes_sender_name(tg, accounts):
    await tg.send(member(ALICE, username=None, first_name="<b>Алиса</b>"), "/transfer @user502 1")
    [reply] = tg.requests(SendMessage)
    assert "&lt;b&gt;Алиса&lt;/b&gt;" in reply.text
    assert "<b>" not in reply.text


@pytest.mark.parametrize(
    ("args", "text_key"),
    [
        ("@user502 500", "transfer_not_enough"),
        ("@user502 0", "transfer_invalid_amount"),
        ("@user502 -5", "transfer_invalid_amount"),
        ("@ghost 5", "error_user_not_found"),
        ("@user501 5", "transfer_self"),
        ("@user502", "transfer_usage"),
    ],
)
async def test_transfer_refusals_change_nothing(tg, conn, texts, accounts, args, text_key):
    await tg.send(member(ALICE), f"/transfer {args}")
    [reply] = tg.requests(SendMessage)
    assert reply.text.startswith(texts._t[text_key].split("{")[0])
    assert (await balance(conn, ALICE), await balance(conn, BOB)) == (100, 0)


async def test_history_shows_transfer_from_ledger(tg, accounts):
    await tg.send(member(ALICE), "/transfer @user502 30")
    await tg.send(member(ALICE), "/history")

    history = tg.requests(SendMessage)[-1].text
    # Обе стороны перевода: «кто → кому, эмодзи, изменение»
    assert "<code>@user501</code> → <code>@user502</code> 💸 (-30)" in history
    assert "<code>@user501</code> → <code>@user502</code> 💸 (+30)" in history


# ── /lot ──────────────────────────────────────────────────────────────

ADMIN, BORIS, VERA, GLEB = 601, 602, 603, 604
BIDDERS = (BORIS, VERA, GLEB)


@dataclass
class LotPost:
    message_id: int
    buttons: dict[int, str]  # шаг ставки → callback_data кнопки

    @property
    def lot_id(self) -> str:
        return self.buttons[5].split(":")[2]


async def balances(conn) -> dict[int, int]:
    return {uid: await balance(conn, uid) for uid in (*BIDDERS, BOT_ID)}


@pytest.fixture
async def lot(tg, conn, config) -> LotPost:
    """Аукцион со стартовой ценой 50; у участников по 100, у бота 0."""
    await seed_users(conn, *BIDDERS, balance=100)
    await tg.send(member(ADMIN, username=config.admin.users[0]), "/lot 10m 50 Тег «Король чата»")
    [post] = tg.requests(SendMessage)
    buttons = {int(button.text.lstrip("+")): button.callback_data for button in post.reply_markup.inline_keyboard[0]}
    return LotPost(message_id=tg.result(post).message_id, buttons=buttons)


async def lot_state(redis, lot: LotPost) -> dict:
    return await RedisStore(redis).lot_game_get(CHAT, lot.lot_id)


async def test_lot_auction(tg, conn, redis, app_container, config, texts, lot):
    assert sorted(lot.buttons) == sorted(config.lot.bid_steps)
    assert [pin.message_id for pin in tg.requests(PinChatMessage)] == [lot.message_id]
    assert await RedisStore(redis).lot_active_get(CHAT) == lot.lot_id

    await tg.click(member(BORIS), lot.message_id, lot.buttons[10])  # 60
    await tg.click(member(VERA), lot.message_id, lot.buttons[5])  # 65, Борису вернули его 60
    await tg.click(member(VERA), lot.message_id, lot.buttons[10])  # 75, Вера доплачивает только 10

    assert await balances(conn) == {BORIS: 100, VERA: 25, GLEB: 100, BOT_ID: 75}
    assert tg.answers() == [f"✅ Твоя ставка: {price} {texts._p.pluralize(price)}!" for price in (60, 65, 75)]
    edits = tg.requests(EditMessageText)
    assert [edit.message_id for edit in edits] == [lot.message_id] * 3
    assert "Лидер: <code>@user603</code> — <b>75 " in edits[-1].text

    # Время вышло: цикл завершает лот, ставка победителя остаётся у бота
    state = await lot_state(redis, lot)
    await RedisStore(redis).lot_game_save(CHAT, lot.lot_id, dict(state, expires_at=time.time() - 1), 60)
    await lot_loop._tick(tg.bot, app_container)

    result = tg.requests(EditMessageText)[-1]
    assert "Аукцион завершён" in result.text and "<code>@user603</code>" in result.text
    assert [unpin.message_id for unpin in tg.requests(UnpinChatMessage)] == [lot.message_id]
    assert await lot_state(redis, lot) is None
    assert await RedisStore(redis).lot_active_get(CHAT) is None
    assert await balances(conn) == {BORIS: 100, VERA: 25, GLEB: 100, BOT_ID: 75}


async def test_lot_only_for_admins(tg, redis):
    await tg.send(member(BORIS), "/lot 10m 50 Тег")
    [reply] = tg.requests(SendMessage)
    assert reply.text.startswith("❌ Только администраторы")
    assert await RedisStore(redis).lot_active_get(CHAT) is None
    assert tg.requests(PinChatMessage) == []


async def test_lot_bid_without_enough_points_changes_nothing(tg, conn, redis, lot):
    await seed_users(conn, GLEB, balance=55)
    await tg.click(member(GLEB), lot.message_id, lot.buttons[10])  # нужно 60

    [answer] = tg.requests(AnswerCallbackQuery)
    assert answer.text.startswith("❌ Недостаточно баллов. Нужно 60") and answer.show_alert
    assert await balances(conn) == {BORIS: 100, VERA: 100, GLEB: 55, BOT_ID: 0}
    state = await lot_state(redis, lot)
    assert (state["leader_id"], state["current_price"], state["bids_count"]) == (None, 50, 0)
    assert tg.requests(EditMessageText) == []


async def bid_together(tg, lot: LotPost, *bids: tuple[int, int]) -> None:
    await asyncio.gather(*(tg.click(member(uid), lot.message_id, lot.buttons[step]) for uid, step in bids))


async def assert_money_matches_lot(conn, redis, lot: LotPost) -> dict:
    """У бота ровно ставка лидера, всем остальным участникам их баллы вернули."""
    state = await lot_state(redis, lot)
    expected = {uid: 100 for uid in BIDDERS} | {BOT_ID: state["current_price"]}
    expected[state["leader_id"]] -= state["current_price"]
    assert await balances(conn) == expected
    return state


async def test_lot_bid_during_another_bid_hits_lock(tg, conn, redis, monkeypatch, lot):
    await tg.click(member(BORIS), lot.message_id, lot.buttons[10])  # лидер — Борис, 60

    # Ставка, взявшая лок, ждёт, пока вторая в него упрётся
    refused = asyncio.Event()
    real_acquire, real_get = RedisStore.lot_lock_acquire, RedisStore.lot_game_get

    async def acquire(self, chat_id, lot_id):
        acquired = await real_acquire(self, chat_id, lot_id)
        if not acquired:
            refused.set()
        return acquired

    async def get_after_other_refused(self, chat_id, lot_id):
        await asyncio.wait_for(refused.wait(), 5)
        return await real_get(self, chat_id, lot_id)

    with monkeypatch.context() as m:
        m.setattr(RedisStore, "lot_lock_acquire", acquire)
        m.setattr(RedisStore, "lot_game_get", get_after_other_refused)
        await bid_together(tg, lot, (VERA, 5), (GLEB, 25))

    state = await assert_money_matches_lot(conn, redis, lot)
    assert state["bids_count"] == 2
    assert "⏳ Подожди секунду…" in tg.answers()


async def test_lot_bids_racing_after_lock_expiry_settle_once(tg, conn, redis, monkeypatch, lot):
    """Лок ставки живёт 5 с. Если он истёк, пока ставка ждала БД, обе ставки читают один
    и тот же лот: записать его должна только одна, вторая откатывается вместе с деньгами."""
    await tg.click(member(BORIS), lot.message_id, lot.buttons[10])  # лидер — Борис, 60

    both_read = asyncio.Barrier(2)
    real_get = RedisStore.lot_game_get

    async def lock_expired(self, chat_id, lot_id):
        return True

    async def get_and_wait_for_the_other(self, chat_id, lot_id):
        data = await real_get(self, chat_id, lot_id)
        await asyncio.wait_for(both_read.wait(), 5)
        return data

    with monkeypatch.context() as m:
        m.setattr(RedisStore, "lot_lock_acquire", lock_expired)
        m.setattr(RedisStore, "lot_game_get", get_and_wait_for_the_other)
        await bid_together(tg, lot, (VERA, 5), (GLEB, 25))

    state = await assert_money_matches_lot(conn, redis, lot)
    assert state["bids_count"] == 2
    assert state["leader_id"] in (VERA, GLEB)
    assert "⏳ Ставку уже перебили — попробуй ещё раз." in tg.answers()
