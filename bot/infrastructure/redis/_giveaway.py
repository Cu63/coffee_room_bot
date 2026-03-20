"""Периодические гивэвеи."""

from __future__ import annotations

import json
import time

_GIVEAWAY_PERIOD = "giveaway_period:"


class GiveawayStoreMixin:

    def _gp_key(self, chat_id: int, gp_id: str) -> str:
        return f"{_GIVEAWAY_PERIOD}{chat_id}:{gp_id}"

    async def giveaway_period_create(
        self, chat_id: int, created_by: int, prizes: list[int],
        period_seconds: int, round_duration_seconds: int | None,
    ) -> str:
        import random as _random
        gp_id = str(_random.randint(10000, 99999))
        key = self._gp_key(chat_id, gp_id)
        data = json.dumps({
            "gp_id": gp_id, "chat_id": chat_id, "created_by": created_by,
            "prizes": prizes, "period_seconds": period_seconds,
            "round_duration_seconds": round_duration_seconds,
            "next_run": time.time(),
        })
        await self._r.set(key, data)
        return gp_id

    async def giveaway_period_list(self, chat_id: int) -> list[tuple[str, dict]]:
        results = []
        async for key in self._r.scan_iter(f"{_GIVEAWAY_PERIOD}{chat_id}:*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            results.append((data["gp_id"], data))
        return results

    async def giveaway_period_all(self) -> list[dict]:
        results = []
        async for key in self._r.scan_iter(f"{_GIVEAWAY_PERIOD}*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            results.append(json.loads(raw))
        return results

    async def giveaway_period_update_next_run(self, chat_id: int, gp_id: str, next_run: float) -> None:
        key = self._gp_key(chat_id, gp_id)
        raw = await self._r.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["next_run"] = next_run
        await self._r.set(key, json.dumps(data))

    async def giveaway_period_delete(self, chat_id: int, gp_id: str) -> dict | None:
        key = self._gp_key(chat_id, gp_id)
        raw = await self._r.get(key)
        await self._r.delete(key)
        return json.loads(raw) if raw else None
