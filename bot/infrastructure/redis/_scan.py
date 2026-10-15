"""Просроченные игры (BJ, TTT, дуэльные приглашения) и безопасные обновления состояния."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

from redis.exceptions import WatchError

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

    async def game_patch_if_state(self, key: str, state: str, updates: dict) -> bool:
        """Дописать поля в JSON-состояние игры, только если игра всё ещё в state.

        WATCH/MULTI: если состояние успели изменить (например, вызов приняли),
        ничего не пишем и не затираем новую версию. Срок жизни ключа сохраняется.
        """
        async with self._r.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if raw is None:
                    return False
                data = json.loads(raw)
                if data.get("state") != state:
                    return False
                data.update(updates)
                pipe.multi()
                pipe.set(key, json.dumps(data), keepttl=True)
                await pipe.execute()
                return True
            except WatchError:
                return False
