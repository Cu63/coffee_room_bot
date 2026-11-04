"""Slots: кулдаун и all-ставка раз в сутки."""

from __future__ import annotations

import time

_SLOTS_LAST = "slots:last:"
_SLOTS_ALL_DAILY = "slots:all:"


class SlotsStoreMixin:
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
