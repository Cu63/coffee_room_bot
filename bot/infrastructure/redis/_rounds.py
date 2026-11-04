"""Раунды с вариантами ответа (/quiz, /whosaid): один раунд на чат, один ответ на участника."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

# Отпустить чат, только если его держит этот раунд
_RELEASE_IF_OWNER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


class RoundStoreMixin:
    _ROUND = "round:game:"
    _ROUND_CHAT = "round:chat:"
    _ROUND_ANSWERED = "round:answered:"
    _ROUND_CD = "round:cd:"
    _ROUND_DAILY = "round:daily:"

    async def round_cooldown_left(self, kind: str, chat_id: int) -> int | None:
        ttl = await self._r.ttl(f"{self._ROUND_CD}{kind}:{chat_id}")
        return ttl if ttl and ttl > 0 else None

    async def round_daily_count(self, kind: str, chat_id: int, day: str) -> int:
        return int(await self._r.get(f"{self._ROUND_DAILY}{kind}:{chat_id}:{day}") or 0)

    async def round_claim_chat(self, chat_id: int, round_id: str, ttl: int) -> bool:
        """Занять чат под раунд (NX): одновременно в чате идёт один раунд."""
        return bool(await self._r.set(f"{self._ROUND_CHAT}{chat_id}", round_id, nx=True, ex=ttl))

    async def round_release_chat(self, chat_id: int, round_id: str) -> None:
        await self._r.eval(_RELEASE_IF_OWNER, 1, f"{self._ROUND_CHAT}{chat_id}", round_id)

    async def round_save(self, data: dict, ttl: int) -> None:
        """Раунд с логическим expires_at; TTL с запасом, чтобы цикл увидел истёкший раунд раньше Redis."""
        await self._r.set(f"{self._ROUND}{data['id']}", json.dumps(data, ensure_ascii=False), ex=ttl)

    async def round_started(self, kind: str, chat_id: int, day: str, cooldown_seconds: int) -> None:
        pipe = self._r.pipeline()
        if cooldown_seconds > 0:
            pipe.set(f"{self._ROUND_CD}{kind}:{chat_id}", "1", ex=cooldown_seconds)
        key = f"{self._ROUND_DAILY}{kind}:{chat_id}:{day}"
        pipe.incr(key)
        pipe.expire(key, 2 * 86400)
        await pipe.execute()

    async def round_get(self, round_id: str) -> dict | None:
        raw = await self._r.get(f"{self._ROUND}{round_id}")
        return json.loads(raw) if raw else None

    async def round_mark_answered(self, round_id: str, user_id: int, ttl: int) -> bool:
        """Засчитать ответ участника. False — он уже отвечал: в раунде одна попытка."""
        key = f"{self._ROUND_ANSWERED}{round_id}"
        pipe = self._r.pipeline()
        pipe.sadd(key, user_id)
        pipe.expire(key, ttl)
        added, _ = await pipe.execute()
        return bool(added)

    async def round_claim(self, round_id: str) -> dict | None:
        """Забрать раунд (GETDEL): приз платит только тот, кто его забрал."""
        raw = await self._r.getdel(f"{self._ROUND}{round_id}")
        return json.loads(raw) if raw else None

    def round_iter_expired(self) -> AsyncIterator[dict]:
        return self._iter_pop_expired(f"{self._ROUND}*")
