"""Moderation: мут-лимиты, owner_mute, gameban, renew."""

from __future__ import annotations

import time

_OWNER_MUTE = "owner_mute:"
_GAMEBAN = "gameban:"
_SLOTS_DAILY = "slots:daily:"
_SLOTS_LAST = "slots:last:"
_SLOTS_ALL_DAILY = "slots:all:"


class ModerationStoreMixin:

    _MUTE_DAILY = "mute:daily:"
    _MUTE_TARGET = "mute:target:"
    _RENEW_DAILY = "renew:daily:"

    async def mute_daily_count(self, actor_id: int, chat_id: int) -> int:
        key = f"{self._MUTE_DAILY}{actor_id}:{chat_id}"
        raw = await self._r.get(key)
        return int(raw or 0)

    async def mute_daily_increment(self, actor_id: int, chat_id: int) -> None:
        key = f"{self._MUTE_DAILY}{actor_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400)
        await pipe.execute()

    async def mute_target_cooldown_ok(self, actor_id: int, target_id: int, chat_id: int) -> bool:
        key = f"{self._MUTE_TARGET}{actor_id}:{target_id}:{chat_id}"
        return not bool(await self._r.exists(key))

    async def mute_target_cooldown_set(self, actor_id: int, target_id: int, chat_id: int, hours: int) -> None:
        key = f"{self._MUTE_TARGET}{actor_id}:{target_id}:{chat_id}"
        await self._r.set(key, "1", ex=hours * 3600)

    # ── /renew ─────────────────────────────────────────────

    async def renew_daily_count(self, user_id: int, chat_id: int) -> int:
        key = f"{self._RENEW_DAILY}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        return int(raw or 0)

    async def renew_daily_increment(self, user_id: int, chat_id: int) -> None:
        key = f"{self._RENEW_DAILY}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400)
        await pipe.execute()

    async def renew_game_limits(self, user_id: int, chat_id: int) -> None:
        await self._r.delete(
            f"{_SLOTS_LAST}{user_id}:{chat_id}",
            f"{_SLOTS_DAILY}{user_id}:{chat_id}",
            f"{_SLOTS_ALL_DAILY}{user_id}:{chat_id}",
        )

    # ── Самозапрет на игры ────────────────────────────────

    async def gameban_set(self, user_id: int, chat_id: int, until_ts: float) -> None:
        key = f"{_GAMEBAN}{user_id}:{chat_id}"
        ttl = max(int(until_ts - time.time()) + 10, 60)
        await self._r.set(key, str(until_ts), ex=ttl)

    async def gameban_active(self, user_id: int, chat_id: int) -> bool:
        key = f"{_GAMEBAN}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return False
        return float(raw) > time.time()

    async def gameban_get_until(self, user_id: int, chat_id: int) -> float | None:
        key = f"{_GAMEBAN}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return None
        ts = float(raw)
        return ts if ts > time.time() else None

    async def gameban_delete(self, user_id: int, chat_id: int) -> None:
        await self._r.delete(f"{_GAMEBAN}{user_id}:{chat_id}")

    # ── Owner mute ────────────────────────────────────────

    async def owner_mute_set(self, chat_id: int, user_id: int, until_ts: float) -> None:
        key = f"{_OWNER_MUTE}{chat_id}:{user_id}"
        ttl = max(int(until_ts - time.time()) + 10, 60)
        await self._r.set(key, str(until_ts), ex=ttl)

    async def owner_mute_active(self, chat_id: int, user_id: int) -> bool:
        key = f"{_OWNER_MUTE}{chat_id}:{user_id}"
        raw = await self._r.get(key)
        if raw is None:
            return False
        return float(raw) > time.time()

    async def owner_mute_get_ts(self, chat_id: int, user_id: int) -> float | None:
        key = f"{_OWNER_MUTE}{chat_id}:{user_id}"
        raw = await self._r.get(key)
        return float(raw) if raw else None

    async def owner_mute_delete(self, chat_id: int, user_id: int) -> None:
        await self._r.delete(f"{_OWNER_MUTE}{chat_id}:{user_id}")
