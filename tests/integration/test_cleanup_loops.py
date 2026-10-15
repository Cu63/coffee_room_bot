"""Фоновые циклы: расчёт истёкших игр, налог и изоляция ошибок."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

import bot.infrastructure.dice_loop as dice_loop_module
from bot.application.dice_service import DiceService
from bot.application.interfaces.message_repository import MessageInfo
from bot.infrastructure import bj_cleanup_loop, duel_cleanup_loop, tax_loop, ttt_cleanup_loop
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from tests.integration.helpers import BOT_ID, CHAT, balance, seed_users

P1, P2 = 301, 302


class FakeBot:
    id = BOT_ID

    def __init__(self) -> None:
        self.edits: list[tuple[int, int, str]] = []
        self.sent: list[tuple[int, str]] = []

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None):
        self.edits.append((chat_id, message_id, text))

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


@pytest.fixture
async def players(conn):
    await seed_users(conn, P1, P2, BOT_ID, balance=0)


@pytest.mark.parametrize(
    ("loop", "data", "expected"),
    [
        # Лобби никто не принял — ставка возвращается создателю
        (bj_cleanup_loop, {"state": "lobby", "bet": 30, "p1_id": P1}, {P1: 30, P2: 0, BOT_ID: 0}),
        (ttt_cleanup_loop, {"state": "lobby", "bet": 20, "player_x": P1}, {P1: 20, P2: 0, BOT_ID: 0}),
        # Начатую игру бросили — обе ставки забирает бот
        (bj_cleanup_loop, {"state": "playing", "bet": 30, "p1_id": P1, "p2_id": P2}, {P1: 0, P2: 0, BOT_ID: 60}),
        (ttt_cleanup_loop, {"state": "playing", "bet": 20, "player_x": P1, "player_o": P2}, {P1: 0, P2: 0, BOT_ID: 40}),
    ],
)
async def test_expired_game_settlement(app_container, conn, players, loop, data, expected):
    bot = FakeBot()
    await loop._settle_expired(app_container, bot, dict(data, chat_id=CHAT, message_id=7))
    assert {uid: await balance(conn, uid) for uid in expected} == expected
    assert len(bot.edits) == 1 and bot.edits[0][:2] == (CHAT, 7)


async def test_expired_duel_game_settles_under_invite_ref(app_container, conn, players):
    data = {"state": "playing", "bet": 20, "player_x": P1, "player_o": P2, "game_id": "g", "ref": "duel:42"}
    await ttt_cleanup_loop._settle_expired(app_container, FakeBot(), dict(data, chat_id=CHAT, message_id=7))
    # Ставки игры из /duel списаны с меткой приглашения — с ней же и расчёт
    assert await conn.fetchval("SELECT ref FROM ledger WHERE user_id = $1 AND kind = 'prize'", BOT_ID) == "duel:42"


async def test_expired_duel_invite_refunds_challenger(app_container, conn, players):
    bot = FakeBot()
    await duel_cleanup_loop._refund_expired(
        app_container, bot, {"bet": 15, "chat_id": CHAT, "challenger_id": P1, "message_id": 8}
    )
    assert await balance(conn, P1) == 15
    assert bot.edits and bot.edits[0][:2] == (CHAT, 8)


async def test_dice_unreachable_chat_is_refunded_and_does_not_block_others(app_container, conn, monkeypatch):
    other_chat = CHAT - 1
    await seed_users(conn, P1, P2, balance=100)
    await seed_users(conn, P1, balance=100, chat_id=other_chat)
    ends_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    async with app_container() as scope:
        service = await scope.get(DiceService)
        blocked = (await service.create(CHAT, P1, 10, ends_at)).game
        await service.join(blocked.id, P2, join_cutoff_seconds=0)
        healthy = (await service.create(other_chat, P1, 10, ends_at)).game
    await conn.execute("UPDATE dice_games SET ends_at = now() - interval '1 minute'")

    resolved: list[int] = []

    async def fake_resolve(bot, game, container):
        if game.chat_id == CHAT:
            raise TelegramForbiddenError(method=SendMessage(chat_id=CHAT, text="x"), message="bot was kicked")
        resolved.append(game.id)
        async with container() as scope:
            await (await scope.get(DiceService)).finish(game.id, {game.created_by: 6}, bot_id=BOT_ID)

    ticks = 0
    real_sleep = asyncio.sleep

    async def one_tick(_seconds):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(dice_loop_module, "_resolve_game", fake_resolve)
    monkeypatch.setattr(dice_loop_module.asyncio, "sleep", one_tick)
    with pytest.raises(asyncio.CancelledError):
        await dice_loop_module.dice_loop(FakeBot(), app_container)

    # Игра в чате, куда бот не может писать, закрыта со всеми возвратами
    assert await conn.fetchval("SELECT status FROM dice_games WHERE id = $1", blocked.id) == "finished"
    assert (await balance(conn, P1), await balance(conn, P2)) == (100, 100)
    # Игра в другом чате при этом разыграна
    assert resolved == [healthy.id]


async def test_daily_tax_goes_to_bot(app_container, conn, config):
    rich, middle, poor = 311, 312, 313
    start = {rich: 25000, middle: 10100, poor: 5000}
    for uid, value in start.items():
        await seed_users(conn, uid, balance=value)
    await seed_users(conn, BOT_ID, balance=100000, is_bot=True)  # касса бота налогом не облагается
    await PostgresMessageRepository(conn).save(
        MessageInfo(message_id=1, chat_id=CHAT, user_id=poor, sent_at=datetime.now(timezone.utc), text="привет")
    )

    bot = FakeBot()
    await tax_loop._run_once(bot, app_container)

    tax = {uid: tax_loop._compute_tax(value, config.tax) for uid, value in start.items()}
    assert tax[rich] > tax[middle] > 0 == tax[poor]
    assert {uid: await balance(conn, uid) for uid in start} == {uid: start[uid] - tax[uid] for uid in start}
    assert await balance(conn, BOT_ID) == 100000 + sum(tax.values())
    [(chat_id, text)] = bot.sent
    assert chat_id == CHAT and f"Всего собрано: <b>{sum(tax.values())}</b>" in text
