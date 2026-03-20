"""Анаграмма: кулдаун + управление играми."""

from __future__ import annotations

import json
import time


class AnagramStoreMixin:

    _ANAGRAM_CD = "anagram:cd:"
    _ANAGRAM_GAME = "anagram:game:"
    _ANAGRAM_MSG = "anagram:msg:"
    _ANAGRAM_ACTIVE = "anagram:active:"
    _ANAGRAM_CHATS = "anagram:chats"
    _ANAGRAM_NEXT_AUTO = "anagram:next_auto:"

    async def anagram_cooldown_active(self, chat_id: int) -> int | None:
        ttl = await self._r.ttl(f"{self._ANAGRAM_CD}{chat_id}")
        return ttl if ttl and ttl > 0 else None

    async def anagram_cooldown_set(self, chat_id: int, seconds: int) -> None:
        await self._r.set(f"{self._ANAGRAM_CD}{chat_id}", "1", ex=seconds)

    async def anagram_active_get(self, chat_id: int) -> str | None:
        return await self._r.get(f"{self._ANAGRAM_ACTIVE}{chat_id}")

    async def anagram_active_delete(self, chat_id: int) -> None:
        await self._r.delete(f"{self._ANAGRAM_ACTIVE}{chat_id}")

    async def anagram_game_get(self, game_id: str) -> dict | None:
        raw = await self._r.get(f"{self._ANAGRAM_GAME}{game_id}")
        return json.loads(raw) if raw else None

    async def anagram_game_save(self, game_id: str, data: dict, ttl: int) -> None:
        await self._r.set(f"{self._ANAGRAM_GAME}{game_id}", json.dumps(data), ex=ttl)

    async def anagram_game_delete(self, game_id: str) -> bool:
        return bool(await self._r.delete(f"{self._ANAGRAM_GAME}{game_id}"))

    async def anagram_msg_get(self, chat_id: int, message_id: int) -> str | None:
        return await self._r.get(f"{self._ANAGRAM_MSG}{chat_id}:{message_id}")

    async def anagram_msg_delete(self, chat_id: int, message_id: int) -> None:
        await self._r.delete(f"{self._ANAGRAM_MSG}{chat_id}:{message_id}")

    async def anagram_create_game(
        self, game_id: str, data: dict, chat_id: int, message_id: int, ttl: int,
    ) -> None:
        pipe = self._r.pipeline()
        pipe.set(f"{self._ANAGRAM_GAME}{game_id}", json.dumps(data), ex=ttl)
        pipe.set(f"{self._ANAGRAM_MSG}{chat_id}:{message_id}", game_id, ex=ttl)
        pipe.set(f"{self._ANAGRAM_ACTIVE}{chat_id}", game_id, ex=ttl)
        pipe.zadd(self._ANAGRAM_CHATS, {str(chat_id): time.time()})
        await pipe.execute()

    async def anagram_finish_win(self, game_id: str, chat_id: int, message_id: int) -> bool:
        deleted = bool(await self._r.delete(f"{self._ANAGRAM_GAME}{game_id}"))
        if deleted:
            await self._r.delete(
                f"{self._ANAGRAM_ACTIVE}{chat_id}",
                f"{self._ANAGRAM_MSG}{chat_id}:{message_id}",
            )
        return deleted

    async def anagram_chats_all(self) -> list[tuple[int, float]]:
        entries = await self._r.zrange(self._ANAGRAM_CHATS, 0, -1, withscores=True)
        return [(int(cid), ts) for cid, ts in entries]

    async def anagram_next_auto_get(self, chat_id: int) -> float | None:
        raw = await self._r.get(f"{self._ANAGRAM_NEXT_AUTO}{chat_id}")
        return float(raw) if raw else None

    async def anagram_next_auto_set(self, chat_id: int, ts: float, ttl: int) -> None:
        await self._r.set(f"{self._ANAGRAM_NEXT_AUTO}{chat_id}", str(ts), ex=ttl)

    async def anagram_scan_expired(self) -> list[dict]:
        now = time.time()
        expired: list[dict] = []
        async for key in self._r.scan_iter(f"{self._ANAGRAM_GAME}*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data.get("expires_at", 0) > now:
                continue
            if not await self._r.delete(key):
                continue
            chat_id = data["chat_id"]
            msg_id = data.get("message_id", 0)
            await self._r.delete(f"{self._ANAGRAM_ACTIVE}{chat_id}")
            if msg_id:
                await self._r.delete(f"{self._ANAGRAM_MSG}{chat_id}:{msg_id}")
            expired.append(data)
        return expired
