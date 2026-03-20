"""Slots: дневной лимит, кулдаун, all-ставка, джекпот."""

from __future__ import annotations

import time

_SLOTS_DAILY = "slots:daily:"
_SLOTS_LAST = "slots:last:"
_JACKPOT = "slots:jackpot:"
_SLOTS_ALL_DAILY = "slots:all:"


class SlotsStoreMixin:

    async def slots_daily_check(self, user_id: int, chat_id: int, max_spins: int) -> bool:
        key = f"{_SLOTS_DAILY}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return True
        return int(raw) < max_spins

    async def slots_daily_increment(self, user_id: int, chat_id: int) -> None:
        key = f"{_SLOTS_DAILY}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400)
        await pipe.execute()

    async def slots_cooldown_check(self, user_id: int, chat_id: int, cooldown_seconds: int) -> bool:
        key = f"{_SLOTS_LAST}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return True
        return (time.time() - float(raw)) >= cooldown_seconds

    async def slots_cooldown_set(self, user_id: int, chat_id: int, cooldown_seconds: int) -> None:
        key = f"{_SLOTS_LAST}{user_id}:{chat_id}"
        await self._r.set(key, str(time.time()), ex=cooldown_seconds + 10)

    async def slots_all_used_today(self, user_id: int, chat_id: int) -> bool:
        key = f"{_SLOTS_ALL_DAILY}{user_id}:{chat_id}"
        return bool(await self._r.exists(key))

    async def slots_all_mark_used(self, user_id: int, chat_id: int) -> None:
        key = f"{_SLOTS_ALL_DAILY}{user_id}:{chat_id}"
        await self._r.set(key, "1", ex=86400)

    async def jackpot_add(self, chat_id: int, amount: int) -> None:
        await self._r.incrby(f"{_JACKPOT}{chat_id}", amount)

    async def jackpot_pop(self, chat_id: int) -> int:
        key = f"{_JACKPOT}{chat_id}"
        pipe = self._r.pipeline()
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()
        return int(results[0] or 0)

    async def jackpot_get(self, chat_id: int) -> int:
        raw = await self._r.get(f"{_JACKPOT}{chat_id}")
        return int(raw or 0)
