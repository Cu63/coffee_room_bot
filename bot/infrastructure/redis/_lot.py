"""Аукцион (lot)."""

from __future__ import annotations

import json
import time


class LotStoreMixin:

    _LOT_GAME = "lot:game:"
    _LOT_ACTIVE = "lot:active:"
    _LOT_LOCK = "lot:lock:"

    async def lot_active_get(self, chat_id: int) -> str | None:
        return await self._r.get(f"{self._LOT_ACTIVE}{chat_id}")

    async def lot_active_delete(self, chat_id: int) -> None:
        await self._r.delete(f"{self._LOT_ACTIVE}{chat_id}")

    async def lot_game_exists(self, chat_id: int, lot_id: str) -> bool:
        return bool(await self._r.exists(f"{self._LOT_GAME}{chat_id}:{lot_id}"))

    async def lot_game_get(self, chat_id: int, lot_id: str) -> dict | None:
        raw = await self._r.get(f"{self._LOT_GAME}{chat_id}:{lot_id}")
        return json.loads(raw) if raw else None

    async def lot_game_save(self, chat_id: int, lot_id: str, data: dict, ttl: int) -> None:
        await self._r.set(
            f"{self._LOT_GAME}{chat_id}:{lot_id}",
            json.dumps(data, ensure_ascii=False), ex=ttl,
        )

    async def lot_game_delete(self, chat_id: int, lot_id: str) -> bool:
        return bool(await self._r.delete(f"{self._LOT_GAME}{chat_id}:{lot_id}"))

    async def lot_create(self, chat_id: int, lot_id: str, data: dict, ttl: int) -> None:
        pipe = self._r.pipeline()
        pipe.set(
            f"{self._LOT_GAME}{chat_id}:{lot_id}",
            json.dumps(data, ensure_ascii=False), ex=ttl,
        )
        pipe.set(f"{self._LOT_ACTIVE}{chat_id}", lot_id, ex=ttl)
        await pipe.execute()

    async def lot_lock_acquire(self, chat_id: int, lot_id: str) -> bool:
        return bool(await self._r.set(f"{self._LOT_LOCK}{chat_id}:{lot_id}", "1", nx=True, ex=5))

    async def lot_lock_release(self, chat_id: int, lot_id: str) -> None:
        await self._r.delete(f"{self._LOT_LOCK}{chat_id}:{lot_id}")

    async def lot_scan_expired(self) -> list[dict]:
        now = time.time()
        expired: list[dict] = []
        async for key in self._r.scan_iter(f"{self._LOT_GAME}*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data.get("expires_at", 0) > now:
                continue
            expired.append(data)
        return expired
