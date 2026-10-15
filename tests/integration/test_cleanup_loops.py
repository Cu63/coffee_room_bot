"""Фоновые циклы: расчёт истёкших игр и изоляция ошибок."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

import bot.infrastructure.dice_loop as dice_loop_module
from bot.application.dice_service import DiceService
from bot.infrastructure import bj_cleanup_loop, duel_cleanup_loop, ttt_cleanup_loop
from tests.integration.helpers import BOT_ID, CHAT, balance, seed_users

P1, P2 = 301, 302


class FakeBot:
    id = BOT_ID

    def __init__(self) -> None:
        self.edits: list[tuple[int, int, str]] = []

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None):
        self.edits.append((chat_id, message_id, text))


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
