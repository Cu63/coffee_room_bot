"""Сканирование просроченных BJ-дуэлей и дуэльных приглашений."""

from __future__ import annotations

import json
import time


class ScanStoreMixin:

    async def bj_duel_pop_expired(self) -> list[dict]:
        now = time.time()
        expired: list[dict] = []
        async for key in self._r.scan_iter("bj:duel:*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data.get("expires_at", 0.0) > now:
                continue
            if not await self._r.delete(key):
                continue
            expired.append(data)
        return expired

    async def duel_invite_pop_expired(self) -> list[dict]:
        now = time.time()
        expired: list[dict] = []
        async for key in self._r.scan_iter("duel:invite:*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data.get("expires_at", 0.0) > now:
                continue
            if not await self._r.delete(key):
                continue
            expired.append(data)
        return expired
