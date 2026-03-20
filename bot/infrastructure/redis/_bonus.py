"""Bonus: burst, spark, reply chain."""

from __future__ import annotations

import time

_BURST_WINDOW = "burst:win:"
_BURST_COOLDOWN = "burst:cd:"
_SPARK_ACTIVE = "spark:act:"
_SPARK_RESP = "spark:resp:"
_SPARK_COOLDOWN = "spark:cd:"
_CHAIN_COUNT = "chain:cnt:"
_CHAIN_LAST = "chain:last:"
_CHAIN_COOLDOWN = "chain:cd:"


class BonusStoreMixin:

    # ── Burst ──────────────────────────────────────────────

    async def burst_cooldown_active(self, user_id: int, chat_id: int) -> bool:
        return bool(await self._r.exists(f"{_BURST_COOLDOWN}{user_id}:{chat_id}"))

    async def burst_add_message(self, user_id: int, chat_id: int, window_seconds: int) -> int:
        key = f"{_BURST_WINDOW}{user_id}:{chat_id}"
        now = time.time()
        cutoff = now - window_seconds
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 60)
        results = await pipe.execute()
        return results[2]

    async def burst_set_cooldown(self, user_id: int, chat_id: int, cooldown_seconds: int) -> None:
        cd_key = f"{_BURST_COOLDOWN}{user_id}:{chat_id}"
        win_key = f"{_BURST_WINDOW}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.set(cd_key, "1", ex=cooldown_seconds)
        pipe.delete(win_key)
        await pipe.execute()

    # ── Spark ──────────────────────────────────────────────

    async def spark_cooldown_active(self, user_id: int, chat_id: int) -> bool:
        return bool(await self._r.exists(f"{_SPARK_COOLDOWN}{user_id}:{chat_id}"))

    async def spark_activate(self, user_id: int, chat_id: int, window_seconds: int) -> None:
        active_key = f"{_SPARK_ACTIVE}{chat_id}"
        resp_key = f"{_SPARK_RESP}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.sadd(active_key, str(user_id))
        pipe.expire(active_key, window_seconds + 60)
        pipe.delete(resp_key)
        pipe.expire(resp_key, window_seconds)
        await pipe.execute()

    async def spark_get_active(self, chat_id: int) -> set[int]:
        members = await self._r.smembers(f"{_SPARK_ACTIVE}{chat_id}")
        return {int(m) for m in members}

    async def spark_add_responder(self, anchor_id: int, responder_id: int, chat_id: int) -> int:
        key = f"{_SPARK_RESP}{anchor_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.sadd(key, str(responder_id))
        pipe.scard(key)
        results = await pipe.execute()
        return results[1]

    async def spark_award_cleanup(self, user_id: int, chat_id: int, cooldown_seconds: int) -> None:
        pipe = self._r.pipeline()
        pipe.set(f"{_SPARK_COOLDOWN}{user_id}:{chat_id}", "1", ex=cooldown_seconds)
        pipe.srem(f"{_SPARK_ACTIVE}{chat_id}", str(user_id))
        pipe.delete(f"{_SPARK_RESP}{user_id}:{chat_id}")
        await pipe.execute()

    # ── Reply chain ────────────────────────────────────────

    def _chain_pair(self, chat_id: int, user_a: int, user_b: int) -> str:
        lo, hi = min(user_a, user_b), max(user_a, user_b)
        return f"{chat_id}:{lo}:{hi}"

    async def chain_cooldown_active(self, chat_id: int, user_a: int, user_b: int) -> bool:
        pair = self._chain_pair(chat_id, user_a, user_b)
        return bool(await self._r.exists(f"{_CHAIN_COOLDOWN}{pair}"))

    async def chain_add_reply(
        self, chat_id: int, replier_id: int, author_id: int, window_seconds: int,
    ) -> int | None:
        pair = self._chain_pair(chat_id, replier_id, author_id)
        last_key = f"{_CHAIN_LAST}{pair}"
        cnt_key = f"{_CHAIN_COUNT}{pair}"
        last = await self._r.get(last_key)
        if last is not None and int(last) == replier_id:
            return None
        pipe = self._r.pipeline()
        pipe.set(last_key, str(replier_id), ex=window_seconds)
        pipe.incr(cnt_key)
        pipe.expire(cnt_key, window_seconds)
        results = await pipe.execute()
        return results[1]

    async def chain_award_cleanup(
        self, chat_id: int, user_a: int, user_b: int, cooldown_seconds: int,
    ) -> None:
        pair = self._chain_pair(chat_id, user_a, user_b)
        pipe = self._r.pipeline()
        pipe.set(f"{_CHAIN_COOLDOWN}{pair}", "1", ex=cooldown_seconds)
        pipe.delete(f"{_CHAIN_COUNT}{pair}", f"{_CHAIN_LAST}{pair}")
        await pipe.execute()
