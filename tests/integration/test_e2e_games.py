"""E2E игр: скрытые руки и атомарные ходы блекджека, ничья в крестиках-ноликах, дуэль."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiogram.methods import EditMessageText, SendMessage

import bot.presentation.handlers.blackjack.callbacks as bj_callbacks
import bot.presentation.handlers.blackjack.helpers as bj_helpers
import bot.presentation.handlers.duel.game_launchers as duel_launchers
import bot.presentation.handlers.tictactoe.callbacks as ttt_callbacks
from bot.application.blackjack_service import Card
from bot.application.stake_service import PotOverdraft
from bot.infrastructure.redis_store import RedisStore
from tests.integration.fake_telegram import member
from tests.integration.helpers import CHAT, balance, seed_users

A, B, OUTSIDER = 721, 722, 723

# 50 ходов исчезающих крестиков-ноликов без трёх в ряд (X ходит первым)
DRAW_LINE = [0, 1, 2, 3, 4, 5, 7, 0, 6, 1, 2, 3, 5, 4, 0, 6, 1, 7, 3, 2, 4, 0, 6, 5, 1,
             3, 2, 7, 4, 0, 5, 1, 6, 3, 2, 4, 0, 7, 5, 6, 1, 2, 3, 0, 4, 5, 6, 1, 7, 2]  # fmt: skip


@pytest.fixture(autouse=True)
def creator_moves_first(monkeypatch):
    """Кто открыл лобби или вызвал на дуэль — ходит первым (p1 / X)."""
    monkeypatch.setattr(bj_callbacks, "random", SimpleNamespace(choice=lambda seq: False))
    monkeypatch.setattr(ttt_callbacks, "random", SimpleNamespace(choice=lambda seq: False))
    monkeypatch.setattr(duel_launchers, "random", SimpleNamespace(shuffle=lambda seq: None))


@pytest.fixture
def deal(monkeypatch):
    """Колода по заказу: две карты p1, две карты p2, дальше — добор по порядку."""
    suits = {"♠": "♠️", "♥": "♥️", "♦": "♦️", "♣": "♣️"}

    def stack(*names: str) -> None:
        order = [Card(name[:-1], suits[name[-1]]) for name in names]
        monkeypatch.setattr(bj_helpers, "build_deck", lambda: list(reversed(order)))  # pop() берёт с конца

    return stack


@pytest.fixture
def loads_together(monkeypatch):
    """Два следующих чтения игры ждут друг друга: оба хода считаются по одному снимку."""

    def arm() -> None:
        both_loaded = asyncio.Barrier(2)
        original_load = RedisStore.game_load
        calls = 0

        async def load(self, key):
            nonlocal calls
            snapshot = await original_load(self, key)
            calls += 1
            if calls <= 2:
                await asyncio.wait_for(both_loaded.wait(), 5)
            return snapshot

        monkeypatch.setattr(RedisStore, "game_load", load)

    return arm


async def open_lobby(tg, command: str) -> tuple[int, str]:
    """A открывает лобби; message_id сообщения и game_id из кнопки «Принять»."""
    await tg.send(member(A), command)
    lobby = tg.requests(SendMessage)[-1]
    accept = lobby.reply_markup.inline_keyboard[0][0].callback_data
    return tg.result(lobby).message_id, accept.split(":")[2]


def last_edit(tg) -> str:
    return tg.requests(EditMessageText)[-1].text


async def pot(conn, ref: str) -> int | None:
    return await conn.fetchval("SELECT amount FROM game_pots WHERE chat_id = $1 AND ref = $2", CHAT, ref)


# ── Блекджек ──────────────────────────────────────────────────────────


async def test_bj_hands_stay_hidden_until_showdown(tg, conn, deal):
    await seed_users(conn, A, B, balance=100)
    deal("10♠", "7♥", "9♦", "8♣", "5♠")
    message_id, game_id = await open_lobby(tg, "/bj 50")

    await tg.click(member(B), message_id, f"bj:accept:{game_id}")
    # Раньше рука того, чей ход, была открыта всему чату — и сопернику тоже
    assert not any(card in last_edit(tg) for card in ("10♠️", "7♥️", "9♦️", "8♣️"))
    assert tg.answers()[-1].endswith("🃏 Твои карты: 9♦️  8♣️ — 17")  # принявшему — во всплывающем окне

    await tg.click(member(A), message_id, f"bj:peek:{game_id}")
    assert tg.answers()[-1] == "🃏 Твои карты: 10♠️  7♥️ — 17"
    await tg.click(member(OUTSIDER), message_id, f"bj:peek:{game_id}")
    assert tg.answers()[-1] == "Ты не участник этой игры."

    await tg.click(member(A), message_id, f"bj:hit:{game_id}")  # 22 — перебор
    assert "Перебор" in tg.answers()[-1]
    assert "💥" not in last_edit(tg) and "5♠️" not in last_edit(tg)  # соперник о переборе не знает

    await tg.click(member(B), message_id, f"bj:stand:{game_id}")
    assert "10♠️" in last_edit(tg) and "💥" in last_edit(tg)  # вскрытие
    assert (await balance(conn, A), await balance(conn, B)) == (50, 150)
    assert await pot(conn, f"bj:{game_id}") == 0  # банк выплачен целиком


async def test_bj_second_settlement_is_refused_by_the_pot(tg, conn, deal, monkeypatch):
    await seed_users(conn, A, B, balance=100)
    deal("10♠", "7♥", "9♦", "8♣")
    message_id, game_id = await open_lobby(tg, "/bj 50")
    await tg.click(member(B), message_id, f"bj:accept:{game_id}")
    await tg.click(member(A), message_id, f"bj:stand:{game_id}")

    # Ошибка в Redis-логике: игра «забирается», но не удаляется — её можно рассчитать ещё раз
    async def claim_without_delete(self, snapshot):
        return True

    monkeypatch.setattr(RedisStore, "game_claim", claim_without_delete)
    await tg.click(member(B), message_id, f"bj:stand:{game_id}")  # 17:17 — ничья, ставки вернулись
    assert (await balance(conn, A), await balance(conn, B)) == (100, 100)

    # Второй расчёт той же игры упирается в пустой банк и откатывается
    with pytest.raises(PotOverdraft):
        await tg.click(member(B), message_id, f"bj:stand:{game_id}")
    assert (await balance(conn, A), await balance(conn, B)) == (100, 100)


async def test_bj_natural_on_accept_settles_at_once(tg, conn, deal, redis):
    await seed_users(conn, A, B, balance=100)
    deal("A♠", "K♥", "9♦", "8♣")
    message_id, game_id = await open_lobby(tg, "/bj 50")

    await tg.click(member(B), message_id, f"bj:accept:{game_id}")

    assert "Блекджек!" in last_edit(tg)
    assert (await balance(conn, A), await balance(conn, B)) == (150, 50)
    assert await redis.exists(f"bj:duel:{CHAT}:{game_id}") == 0


async def test_bj_simultaneous_moves_apply_once(tg, conn, deal, redis, loads_together):
    await seed_users(conn, A, B, balance=100)
    deal("10♠", "2♥", "9♦", "8♣", "3♠")
    message_id, game_id = await open_lobby(tg, "/bj 50")
    await tg.click(member(B), message_id, f"bj:accept:{game_id}")
    answered = len(tg.answers())

    # «Ещё» и «Хватит» по одному снимку игры. Раньше записывались оба хода:
    # игрок видел «Взял 3♠», а в игре оставалось «Хватит» — или наоборот
    loads_together()
    await asyncio.gather(
        tg.click(member(A), message_id, f"bj:hit:{game_id}"),
        tg.click(member(A), message_id, f"bj:stand:{game_id}"),
    )

    assert sorted(bool(text) for text in tg.answers()[answered:]) == [False, True]  # сработал один ход
    game = json.loads(await redis.get(f"bj:duel:{CHAT}:{game_id}"))
    hit_applied = len(game["p1_hand"]) == 3
    assert (game["p1_done"], game["turn"]) == ((False, "p1") if hit_applied else (True, "p2"))


# ── Крестики-нолики ───────────────────────────────────────────────────


async def test_ttt_draw_after_50_moves_refunds_both(tg, conn, redis):
    await seed_users(conn, A, B, balance=100)
    message_id, game_id = await open_lobby(tg, "/ttt 30")
    await tg.click(member(B), message_id, f"ttt:accept:{game_id}")

    for move, cell in enumerate(DRAW_LINE):
        assert await redis.exists(f"ttt:{CHAT}:{game_id}") == 1
        await tg.click(member(B if move % 2 else A), message_id, f"ttt:move:{game_id}:{cell}")

    # Раньше ходы считались по фигурам на поле (не больше шести) — ничьей не бывало
    assert "НИЧЬЯ" in last_edit(tg)
    assert (await balance(conn, A), await balance(conn, B)) == (100, 100)
    assert await redis.exists(f"ttt:{CHAT}:{game_id}") == 0


async def test_ttt_simultaneous_clicks_place_one_piece(tg, conn, redis, loads_together):
    await seed_users(conn, A, B, balance=100)
    message_id, game_id = await open_lobby(tg, "/ttt 30")
    await tg.click(member(B), message_id, f"ttt:accept:{game_id}")
    edits = len(tg.requests(EditMessageText))

    loads_together()
    await asyncio.gather(
        tg.click(member(A), message_id, f"ttt:move:{game_id}:0"),
        tg.click(member(A), message_id, f"ttt:move:{game_id}:4"),
    )

    game = json.loads(await redis.get(f"ttt:{CHAT}:{game_id}"))
    assert sorted(game["board"]) == [0] * 8 + [1]
    assert (game["moves"], game["turn"]) == (1, "o")
    assert len(tg.requests(EditMessageText)) == edits + 1


# ── Дуэль ─────────────────────────────────────────────────────────────


async def test_duel_natural_blackjack_counts_win_under_invite_ref(tg, conn, deal):
    await seed_users(conn, A, B, balance=100)
    deal("A♠", "K♥", "9♦", "8♣")
    await tg.send(member(A), "/duel @user722 bj 50")
    invite = tg.requests(SendMessage)[-1]
    accept = invite.reply_markup.inline_keyboard[0][0].callback_data

    await tg.click(member(B), tg.result(invite).message_id, accept)

    assert "Блекджек!" in last_edit(tg)
    assert (await balance(conn, A), await balance(conn, B)) == (150, 50)
    # Раньше в дуэли вместо статистики стояла заглушка
    assert await conn.fetchval("SELECT wins_blackjack FROM user_stats WHERE user_id = $1", A) == 1
    # Ставки и выплата — под одной меткой приглашения
    rows = await conn.fetch(
        "SELECT kind, sum(delta) AS total FROM ledger WHERE ref = $1 GROUP BY kind", f"duel:{accept.split(':')[2]}"
    )
    assert {row["kind"]: row["total"] for row in rows} == {"stake": -100, "prize": 100}
    assert await pot(conn, f"duel:{accept.split(':')[2]}") == 0
