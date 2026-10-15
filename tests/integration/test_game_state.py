"""Состояние игр в Redis: атомарный захват, «не воскрешать», TTL."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from bot.domain.wordgame_entities import WordGame
from bot.infrastructure.redis_store import RedisStore
from tests.integration.helpers import CHAT


@pytest.fixture
def store(redis):
    return RedisStore(redis)


async def drain(iterator) -> list[str]:
    return [item["game_id"] async for item in iterator]


# ── Угадайка ──────────────────────────────────────────────────────────


async def test_wordgame_pending_is_claimed_once(store, redis):
    game_id = await store.wg_pending_create(1, CHAT, 50, 300)
    assert (await store.wg_pending_get(game_id))["charge_on_submit"] is True
    first, second = await asyncio.gather(store.wg_pending_pop(game_id), store.wg_pending_pop(game_id))
    assert [first is not None, second is not None].count(True) == 1
    await store.wg_pending_set_lobby_msg(game_id, 77)  # не воскрешает забранную игру
    assert await redis.exists(f"wg:pending:{game_id}") == 0


async def test_wordgame_win_is_paid_once_and_game_not_revived(store, redis):
    game = WordGame(game_id="55555", chat_id=CHAT, creator_id=1, word="КОТ", bet=10, ends_at=time.time() + 300)
    await store.wg_game_create(game)
    ttl_before = await redis.ttl("wg:game:55555")

    raw = await store.wg_game_get("55555")
    raw["guesses"].append({"user_id": 2, "word": "КИТ"})
    assert await store.wg_game_save_raw("55555", raw) is True
    assert abs(await redis.ttl("wg:game:55555") - ttl_before) <= 1  # KEEPTTL

    first, second = await asyncio.gather(store.wg_game_finish("55555"), store.wg_game_finish("55555"))
    assert [first is not None, second is not None].count(True) == 1

    assert await store.wg_game_save_raw("55555", raw) is False  # неверная догадка после победы
    assert await redis.exists("wg:game:55555") == 0


# ── Анаграмма ─────────────────────────────────────────────────────────


async def test_anagram_wrong_guess_keeps_ttl_and_does_not_revive(store, redis):
    data = {"game_id": "a1", "chat_id": CHAT, "word": "КОТ", "tries": [], "expires_at": time.time() + 300}
    await store.anagram_create_game("a1", data, CHAT, 5, 330)
    data["tries"].append({"user_id": 2, "word": "ТОК"})
    assert await store.anagram_game_save("a1", data) is True
    # Раньше TTL сокращался до expires_at, и цикл истечения не успевал увидеть игру
    assert await redis.ttl("anagram:game:a1") > 320

    assert await store.anagram_finish_win("a1", CHAT, 5)
    assert await store.anagram_game_save("a1", data) is False
    assert await redis.exists("anagram:game:a1") == 0


# ── Мут-рулетка ───────────────────────────────────────────────────────


async def test_mute_roulette_finished_once_and_not_revived(store, redis):
    roulette_id = await store.mute_roulette_create(CHAT, 1, 5, 1, time.time() + 60)
    assert await store.mute_roulette_join(CHAT, roulette_id, 2) is True
    first, second = await asyncio.gather(
        store.mute_roulette_delete(CHAT, roulette_id), store.mute_roulette_delete(CHAT, roulette_id)
    )
    assert [first is not None, second is not None].count(True) == 1
    assert await store.mute_roulette_join(CHAT, roulette_id, 3) is False
    assert await redis.exists(store._mg_key(CHAT, roulette_id)) == 0


# ── Просроченные игры и лобби ─────────────────────────────────────────


async def test_expired_games_are_popped_once(store, redis):
    now = time.time()
    await redis.set(f"bj:duel:{CHAT}:old", json.dumps({"game_id": "old", "state": "lobby", "expires_at": now - 1}))
    await redis.set(f"bj:duel:{CHAT}:new", json.dumps({"game_id": "new", "state": "playing", "expires_at": now + 600}))
    await redis.set("bj:duel:broken", "not json")

    first, second = await asyncio.gather(drain(store.bj_duel_iter_expired()), drain(store.bj_duel_iter_expired()))
    assert sorted(first + second) == ["old"]
    assert await redis.exists(f"bj:duel:{CHAT}:new") == 1
    assert await redis.exists("bj:duel:broken") == 1


async def test_ttt_expired_scan_skips_locks(store, redis):
    await redis.set(f"ttt:{CHAT}:t1", json.dumps({"game_id": "t1", "state": "lobby", "expires_at": time.time() - 1}))
    await redis.set(f"ttt:lock:{CHAT}:t1", "1")
    await redis.set(f"ttt:lock:accept:{CHAT}:t1", "1")
    assert await drain(store.ttt_iter_expired()) == ["t1"]
    assert await redis.exists(f"ttt:lock:{CHAT}:t1") == 1


async def test_lobby_message_id_does_not_overwrite_accepted_game(store, redis):
    key = f"bj:duel:{CHAT}:lobby"
    await redis.set(key, json.dumps({"state": "lobby", "message_id": 0}), ex=500)
    assert await store.game_patch_if_state(key, "lobby", {"message_id": 42}) is True
    assert json.loads(await redis.get(key))["message_id"] == 42
    assert await redis.ttl(key) > 490

    await redis.set(key, json.dumps({"state": "playing", "p2_id": 2, "message_id": 0}), ex=500)
    assert await store.game_patch_if_state(key, "lobby", {"message_id": 43}) is False
    assert json.loads(await redis.get(key))["p2_id"] == 2


async def test_game_moves_and_finish_need_a_fresh_snapshot(store, redis):
    key = f"bj:duel:{CHAT}:cas"
    assert await store.game_create(key, {"state": "playing", "turn": "p1"}, 500)
    assert await store.game_create(key, {"state": "lobby"}, 500) is None  # ключ занят

    read = await store.game_load(key)
    moved = await store.game_save(read, {"state": "playing", "turn": "p2"}, 600)
    assert moved is not None and 590 < await redis.ttl(key) <= 600

    # Второе нажатие по старому снимку: ни хода, ни расчёта
    assert await store.game_save(read, {"state": "playing", "turn": "p1", "extra": 1}, 600) is None
    assert await store.game_claim(read) is False
    assert json.loads(await redis.get(key))["turn"] == "p2"

    assert await store.game_claim(moved) is True
    assert await store.game_claim(moved) is False  # рассчитывает ровно один
    assert await store.game_save(moved, {"state": "playing"}, 600) is None  # завершённую не воскрешаем
    assert await redis.exists(key) == 0


async def test_lobby_is_accepted_once(store, redis):
    key = f"ttt:{CHAT}:lobby"
    await store.game_create(key, {"state": "lobby", "message_id": 0}, 500)
    await store.game_patch_if_state(key, "lobby", {"message_id": 42})  # снимок лобби поменялся — не помеха

    first, second = await asyncio.gather(
        store.game_replace_if_state(key, "lobby", {"state": "playing", "player_o": 1}, 600),
        store.game_replace_if_state(key, "lobby", {"state": "playing", "player_o": 2}, 600),
    )
    assert [first is not None, second is not None].count(True) == 1
    assert await store.game_replace_if_state(f"ttt:{CHAT}:gone", "lobby", {"state": "playing"}, 600) is None


# ── Лот ───────────────────────────────────────────────────────────────


async def test_lot_saves_only_from_fresh_snapshot(store, redis):
    lot = {"lot_id": "l1", "chat_id": CHAT, "current_price": 50, "leader_id": None, "bids_count": 0,
           "description": "Тег «Король»", "expires_at": time.time() + 600}  # fmt: skip
    await store.lot_create(CHAT, "l1", lot, 660)
    snapshot = await store.lot_game_get(CHAT, "l1")

    assert await store.lot_game_save_if_unchanged(
        CHAT, "l1", 0, dict(snapshot, current_price=55, leader_id=1, bids_count=1), 600
    )
    # Вторая ставка по тому же снимку — отклонена
    assert not await store.lot_game_save_if_unchanged(
        CHAT, "l1", 0, dict(snapshot, current_price=60, leader_id=2, bids_count=1), 600
    )
    current = await store.lot_game_get(CHAT, "l1")
    assert (current["leader_id"], current["current_price"]) == (1, 55)

    # Ставка после завершения лота — отклонена, лот не воскресает
    assert await store.lot_game_delete(CHAT, "l1")
    assert not await store.lot_game_save_if_unchanged(CHAT, "l1", 1, dict(current, bids_count=2), 600)
    assert await redis.exists(f"lot:game:{CHAT}:l1") == 0


# ── «Искра» ───────────────────────────────────────────────────────────


async def test_spark_window_expires(store, redis):
    await store.spark_activate(1, CHAT, 1)
    assert await store.spark_get_active(CHAT) == {1}
    assert await store.spark_add_responder(1, 2, CHAT, 1) == 1
    assert 0 < await redis.ttl(f"spark:resp:1:{CHAT}") <= 1  # раньше TTL не ставился

    await asyncio.sleep(1.3)
    assert await store.spark_get_active(CHAT) == set()
    assert not await redis.sismember(f"spark:act:{CHAT}", "1")


async def test_spark_award_puts_anchor_on_cooldown(store):
    await store.spark_activate(1, CHAT, 60)
    await store.spark_award_cleanup(1, CHAT, 60)
    assert await store.spark_get_active(CHAT) == set()
    assert await store.spark_cooldown_active(1, CHAT)
