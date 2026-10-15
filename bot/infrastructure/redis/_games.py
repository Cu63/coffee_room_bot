"""Пошаговые игры (BJ, TTT): атомарные переходы состояния.

Каждый переход — compare-and-set по снимку, который прочитал хендлер. Ход, посчитанный
по устаревшему состоянию (двойное нажатие, два хода разом), не запишется, а игру
рассчитает и оплатит ровно один из тех, кто её завершал. Просроченные игры по одной
забирает цикл истечения (_scan.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from redis.exceptions import WatchError

# Записать, только если ключ всё ещё содержит прочитанный снимок
_SAVE_IF_UNCHANGED = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""

# Удалить, только если ключ всё ещё содержит прочитанный снимок
_DELETE_IF_UNCHANGED = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""

# Заменить, только если игра всё ещё в состоянии ARGV[1] (лобби → игра)
_REPLACE_IF_STATE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local ok, data = pcall(cjson.decode, raw)
if not ok or type(data) ~= 'table' or data['state'] ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""


@dataclass(slots=True, frozen=True)
class GameSnapshot:
    """Прочитанное состояние игры. data можно менять: сравнение идёт по raw."""

    key: str
    raw: str
    data: dict


class GameStoreMixin:
    async def game_create(self, key: str, data: dict, ttl: int) -> GameSnapshot | None:
        """Новая игра; None — ключ уже занят."""
        raw = json.dumps(data)
        return GameSnapshot(key, raw, data) if await self._r.set(key, raw, ex=ttl, nx=True) else None

    async def game_load(self, key: str) -> GameSnapshot | None:
        raw = await self._r.get(key)
        return None if raw is None else GameSnapshot(key, raw, json.loads(raw))

    async def game_save(self, snapshot: GameSnapshot, data: dict, ttl: int) -> GameSnapshot | None:
        """Ход. None — с момента чтения игру изменили, рассчитали или забрал цикл истечения."""
        raw = json.dumps(data)
        saved = await self._r.eval(_SAVE_IF_UNCHANGED, 1, snapshot.key, snapshot.raw, raw, ttl)
        return GameSnapshot(snapshot.key, raw, data) if saved else None

    async def game_claim(self, snapshot: GameSnapshot) -> bool:
        """Завершение: забрать игру для расчёта. True получит ровно один — он и платит."""
        return bool(await self._r.eval(_DELETE_IF_UNCHANGED, 1, snapshot.key, snapshot.raw))

    async def game_replace_if_state(self, key: str, state: str, data: dict, ttl: int) -> GameSnapshot | None:
        """Переход из state (лобби → игра). None — игру уже приняли или лобби истекло."""
        raw = json.dumps(data)
        replaced = await self._r.eval(_REPLACE_IF_STATE, 1, key, state, raw, ttl)
        return GameSnapshot(key, raw, data) if replaced else None

    async def game_delete(self, key: str) -> bool:
        """Забрать ключ целиком (приглашение на дуэль): True получит ровно один."""
        return bool(await self._r.delete(key))

    async def game_lock(self, key: str, ttl: int) -> bool:
        """Одноразовая бронь (принятие лобби): второй нажавший не будет списан."""
        return bool(await self._r.set(key, "1", nx=True, ex=ttl))

    async def game_unlock(self, key: str) -> None:
        await self._r.delete(key)

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
