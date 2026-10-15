"""Просроченные игры (BJ, TTT, дуэльные приглашения): цикл истечения забирает их по одной."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

# Атомарно: удалить ключ и вернуть значение, только если в нём JSON с expires_at <= now.
# Цикл не заберёт игру по устаревшему снимку, если ход только что продлил expires_at.
_POP_IF_EXPIRED = """
local raw = redis.call('GET', KEYS[1])
if not raw then return false end
local ok, data = pcall(cjson.decode, raw)
if not ok or type(data) ~= 'table' then return false end
local expires_at = tonumber(data['expires_at'])
if expires_at == nil or expires_at > tonumber(ARGV[1]) then return false end
redis.call('DEL', KEYS[1])
return raw
"""


class ScanStoreMixin:
    async def _iter_pop_expired(self, pattern: str, *, skip_prefix: str | None = None) -> AsyncIterator[dict]:
        """По одной забирает просроченные игры. Ключ удаляется только вместе с выдачей данных:
        параллельный хендлер, который удаляет ключ при завершении, и цикл не обработают игру дважды.
        """
        now = time.time()
        async for key in self._r.scan_iter(pattern):
            if skip_prefix and key.startswith(skip_prefix):
                continue
            raw = await self._r.eval(_POP_IF_EXPIRED, 1, key, now)
            if raw:
                yield json.loads(raw)

    def bj_duel_iter_expired(self) -> AsyncIterator[dict]:
        return self._iter_pop_expired("bj:duel:*")

    def duel_invite_iter_expired(self) -> AsyncIterator[dict]:
        return self._iter_pop_expired("duel:invite:*")

    def ttt_iter_expired(self) -> AsyncIterator[dict]:
        return self._iter_pop_expired("ttt:*", skip_prefix="ttt:lock:")
