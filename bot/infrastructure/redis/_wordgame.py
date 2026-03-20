"""Угадайка (wordgame)."""

from __future__ import annotations

import json
import time


class WordgameStoreMixin:

    _WG_PENDING = "wg:pending:"
    _WG_AWAITING = "wg:awaiting:"
    _WG_GAME = "wg:game:"
    _WG_CHAT = "wg:chat:"
    _WG_RATE = "wg:rate:"
    _WG_TTL = 600
    _WG_RWORD_CD = "wg:rword_cd:"

    async def wg_pending_create(self, user_id: int, chat_id: int, bet: int, duration_seconds: int) -> str:
        import random as _r
        game_id = str(_r.randint(10000, 99999))
        data = json.dumps({
            "game_id": game_id, "user_id": user_id, "chat_id": chat_id,
            "bet": bet, "duration_seconds": duration_seconds, "lobby_msg_id": 0,
        })
        await self._r.set(f"{self._WG_PENDING}{game_id}", data, ex=self._WG_TTL)
        return game_id

    async def wg_pending_get(self, game_id: str) -> dict | None:
        raw = await self._r.get(f"{self._WG_PENDING}{game_id}")
        return json.loads(raw) if raw else None

    async def wg_pending_set_lobby_msg(self, game_id: str, message_id: int) -> None:
        key = f"{self._WG_PENDING}{game_id}"
        raw = await self._r.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["lobby_msg_id"] = message_id
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 30))

    async def wg_pending_delete(self, game_id: str) -> None:
        await self._r.delete(f"{self._WG_PENDING}{game_id}")

    async def wg_awaiting_set(self, user_id: int, game_id: str) -> None:
        await self._r.set(f"{self._WG_AWAITING}{user_id}", game_id, ex=self._WG_TTL)

    async def wg_awaiting_get(self, user_id: int) -> str | None:
        return await self._r.get(f"{self._WG_AWAITING}{user_id}")

    async def wg_awaiting_delete(self, user_id: int) -> None:
        await self._r.delete(f"{self._WG_AWAITING}{user_id}")

    async def wg_rate_check(self, user_id: int, max_games: int, window_seconds: int) -> int:
        key = f"{self._WG_RATE}{user_id}"
        now = time.time()
        await self._r.zremrangebyscore(key, 0, now - window_seconds)
        return await self._r.zcard(key)

    async def wg_rate_record(self, user_id: int, window_seconds: int) -> None:
        key = f"{self._WG_RATE}{user_id}"
        now = time.time()
        await self._r.zadd(key, {str(now): now})
        await self._r.expire(key, window_seconds)

    async def wg_rword_cooldown_active(self, chat_id: int) -> int | None:
        ttl = await self._r.ttl(f"{self._WG_RWORD_CD}{chat_id}")
        return ttl if ttl and ttl > 0 else None

    async def wg_rword_cooldown_set(self, chat_id: int, seconds: int) -> None:
        await self._r.set(f"{self._WG_RWORD_CD}{chat_id}", "1", ex=seconds)

    async def wg_rate_check_rword(self, user_id: int, max_games: int, window_seconds: int) -> int:
        key = f"{self._WG_RATE}rword:{user_id}"
        now = time.time()
        await self._r.zremrangebyscore(key, 0, now - window_seconds)
        return await self._r.zcard(key)

    async def wg_rate_record_rword(self, user_id: int, window_seconds: int) -> None:
        key = f"{self._WG_RATE}rword:{user_id}"
        now = time.time()
        await self._r.zadd(key, {str(now): now})
        await self._r.expire(key, window_seconds)

    async def wg_game_create(self, game) -> None:
        key = f"{self._WG_GAME}{game.game_id}"
        data = {
            "game_id": game.game_id, "chat_id": game.chat_id,
            "creator_id": game.creator_id, "word": game.word,
            "bet": game.bet, "ends_at": game.ends_at,
            "revealed": game.revealed, "guesses": game.guesses,
            "message_id": game.message_id,
            "finished": game.finished, "winner_id": game.winner_id,
            "is_random": game.is_random,
        }
        ttl = int(game.ends_at - time.time()) + 120
        await self._r.set(key, json.dumps(data), ex=max(ttl, 60))

    async def wg_game_get(self, game_id: str) -> dict | None:
        raw = await self._r.get(f"{self._WG_GAME}{game_id}")
        return json.loads(raw) if raw else None

    async def wg_game_save_raw(self, game_id: str, data: dict) -> None:
        key = f"{self._WG_GAME}{game_id}"
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 30))

    async def wg_game_finish(self, game_id: str) -> dict | None:
        key = f"{self._WG_GAME}{game_id}"
        raw = await self._r.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        if data.get("finished"):
            return None
        data["finished"] = True
        await self._r.delete(key)
        return data

    async def wg_chat_add(self, chat_id: int, game_id: str) -> None:
        await self._r.sadd(f"{self._WG_CHAT}{chat_id}", game_id)

    async def wg_chat_remove(self, chat_id: int, game_id: str) -> None:
        await self._r.srem(f"{self._WG_CHAT}{chat_id}", game_id)

    async def wg_chat_games(self, chat_id: int) -> list[str]:
        members = await self._r.smembers(f"{self._WG_CHAT}{chat_id}")
        active = []
        for gid in members:
            if await self._r.exists(f"{self._WG_GAME}{gid}"):
                active.append(gid)
            else:
                await self._r.srem(f"{self._WG_CHAT}{chat_id}", gid)
        return active

    async def wg_game_by_message_id(self, chat_id: int, message_id: int) -> dict | None:
        members = await self._r.smembers(f"{self._WG_CHAT}{chat_id}")
        for gid in members:
            raw = await self._r.get(f"{self._WG_GAME}{gid}")
            if raw is None:
                await self._r.srem(f"{self._WG_CHAT}{chat_id}", gid)
                continue
            data = json.loads(raw)
            if data.get("message_id") == message_id:
                return data
        return None

    async def wg_scan_expired(self) -> list[str]:
        now = time.time()
        expired_ids: list[str] = []
        async for key in self._r.scan_iter(f"{self._WG_GAME}*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data.get("finished") or data.get("ends_at", 0) > now:
                continue
            expired_ids.append(data["game_id"])
        return expired_ids
