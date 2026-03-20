"""Мут-рулетка."""

from __future__ import annotations

import json
import time


class MuteRouletteStoreMixin:

    _MUTE_ROULETTE = "mutegiveaway:"

    def _mg_key(self, chat_id: int, roulette_id: str) -> str:
        return f"{self._MUTE_ROULETTE}{chat_id}:{roulette_id}"

    async def mute_roulette_create(
        self, chat_id: int, creator_id: int, mute_minutes: int,
        losers_count: int, ends_at: float,
    ) -> str:
        import random as _random
        roulette_id = str(_random.randint(10000, 99999))
        key = self._mg_key(chat_id, roulette_id)
        data = json.dumps({
            "roulette_id": roulette_id, "creator_id": creator_id,
            "mute_minutes": mute_minutes, "losers_count": losers_count,
            "ends_at": ends_at, "participants": [], "message_id": 0,
        })
        ttl = int(ends_at - time.time()) + 300
        await self._r.set(key, data, ex=max(ttl, 60))
        return roulette_id

    async def mute_roulette_list(self, chat_id: int) -> list[tuple[str, dict]]:
        results = []
        async for key in self._r.scan_iter(f"{self._MUTE_ROULETTE}{chat_id}:*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            roulette_id = key.split(":")[-1]
            results.append((roulette_id, data))
        return results

    async def mute_roulette_get(self, chat_id: int, roulette_id: str) -> dict | None:
        raw = await self._r.get(self._mg_key(chat_id, roulette_id))
        return json.loads(raw) if raw else None

    async def mute_roulette_join(self, chat_id: int, roulette_id: str, user_id: int) -> bool:
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        if raw is None:
            return False
        data = json.loads(raw)
        if user_id in data["participants"]:
            return False
        data["participants"].append(user_id)
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 60))
        return True

    async def mute_roulette_delete(self, chat_id: int, roulette_id: str) -> dict | None:
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        await self._r.delete(key)
        return json.loads(raw) if raw else None

    async def mute_roulette_set_message_id(self, chat_id: int, roulette_id: str, message_id: int) -> None:
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["message_id"] = message_id
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 60))

    async def mute_roulette_count(self, chat_id: int, roulette_id: str) -> int:
        raw = await self._r.get(self._mg_key(chat_id, roulette_id))
        if raw is None:
            return 0
        return len(json.loads(raw)["participants"])

    async def mute_roulette_pop_expired(self) -> list[tuple[int, str, dict]]:
        now = time.time()
        expired: list[tuple[int, str, dict]] = []
        async for key in self._r.scan_iter(f"{self._MUTE_ROULETTE}*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data["ends_at"] > now:
                continue
            parts = key.split(":")
            chat_id = int(parts[1])
            roulette_id = parts[2]
            finished = await self.mute_roulette_delete(chat_id, roulette_id)
            if finished:
                expired.append((chat_id, roulette_id, finished))
        return expired
