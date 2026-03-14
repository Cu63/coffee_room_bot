"""Redis-backed хранилище для временных данных (игры, лимиты, джекпот)."""

from __future__ import annotations

import json
import logging
import time

import redis.asyncio as aioredis

from bot.application.blackjack_service import BlackjackRound, Card, GameResult

logger = logging.getLogger(__name__)

# Префиксы ключей
_BJ_GAME = "bj:game:"  # bj:game:{user_id}:{chat_id}
_BJ_HISTORY = "bj:hist:"  # bj:hist:{user_id}:{chat_id}  (sorted set)
_SLOTS_DAILY = "slots:daily:"  # slots:daily:{user_id}:{chat_id}
_SLOTS_LAST = "slots:last:"  # slots:last:{user_id}:{chat_id}
_JACKPOT = "slots:jackpot:"  # slots:jackpot:{chat_id}


def _serialize_round(rnd: BlackjackRound) -> str:
    """Сериализация BlackjackRound в JSON."""
    data = {
        "player_id": rnd.player_id,
        "chat_id": rnd.chat_id,
        "bet": rnd.bet,
        "deck": [{"rank": c.rank, "suit": c.suit} for c in rnd.deck],
        "player_hand": [{"rank": c.rank, "suit": c.suit} for c in rnd.player_hand],
        "dealer_hand": [{"rank": c.rank, "suit": c.suit} for c in rnd.dealer_hand],
        "finished": rnd.finished,
        "result": rnd.result.value if rnd.result else None,
    }
    return json.dumps(data, ensure_ascii=False)


def _deserialize_round(raw: str) -> BlackjackRound:
    """Десериализация BlackjackRound из JSON."""
    data = json.loads(raw)
    rnd = BlackjackRound(
        player_id=data["player_id"],
        chat_id=data["chat_id"],
        bet=data["bet"],
        deck=[Card(rank=c["rank"], suit=c["suit"]) for c in data["deck"]],
        player_hand=[Card(rank=c["rank"], suit=c["suit"]) for c in data["player_hand"]],
        dealer_hand=[Card(rank=c["rank"], suit=c["suit"]) for c in data["dealer_hand"]],
        finished=data["finished"],
        result=GameResult(data["result"]) if data["result"] else None,
    )
    return rnd


class RedisStore:
    """Обёртка над Redis для хранения игрового состояния."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    # ── Blackjack: активные игры ─────────────────────────────────

    async def bj_get(self, user_id: int, chat_id: int) -> BlackjackRound | None:
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return None
        return _deserialize_round(raw)

    async def bj_set(self, user_id: int, chat_id: int, rnd: BlackjackRound) -> None:
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        await self._r.set(key, _serialize_round(rnd), ex=3600)  # TTL 1 час

    async def bj_delete(self, user_id: int, chat_id: int) -> None:
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        await self._r.delete(key)

    async def bj_exists(self, user_id: int, chat_id: int) -> bool:
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        return bool(await self._r.exists(key))

    # ── Blackjack: лимит игр (fixed window) ─────────────────────

    async def bj_window_check(
        self,
        user_id: int,
        chat_id: int,
        max_games: int,
    ) -> float | None:
        """Проверить лимит. None — можно играть, иначе — секунд до сброса окна."""
        key = f"{_BJ_HISTORY}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None or int(raw) < max_games:
            return None
        ttl = await self._r.ttl(key)
        return max(ttl, 0)

    async def bj_window_record(self, user_id: int, chat_id: int, window_seconds: int) -> None:
        """Записать игру. TTL устанавливается только при первой игре в окне."""
        key = f"{_BJ_HISTORY}{user_id}:{chat_id}"
        count = await self._r.incr(key)
        if count == 1:
            await self._r.expire(key, window_seconds)

    # ── Slots: дневной лимит ─────────────────────────────────────

    async def slots_daily_check(self, user_id: int, chat_id: int, max_spins: int) -> bool:
        """True если можно крутить, False если лимит исчерпан."""
        key = f"{_SLOTS_DAILY}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return True
        return int(raw) < max_spins

    async def slots_daily_increment(self, user_id: int, chat_id: int) -> None:
        """Инкрементировать счётчик дневных спинов. TTL до конца дня."""
        key = f"{_SLOTS_DAILY}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400)  # 24 часа
        await pipe.execute()

    # ── Slots: кулдаун ───────────────────────────────────────────

    async def slots_cooldown_check(self, user_id: int, chat_id: int, cooldown_seconds: int) -> bool:
        """True если кулдаун прошёл."""
        key = f"{_SLOTS_LAST}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return True
        return (time.time() - float(raw)) >= cooldown_seconds

    async def slots_cooldown_set(self, user_id: int, chat_id: int, cooldown_seconds: int) -> None:
        key = f"{_SLOTS_LAST}{user_id}:{chat_id}"
        await self._r.set(key, str(time.time()), ex=cooldown_seconds + 10)

    # ── Mute: дневной лимит и кулдаун ───────────────────────────────

    _MUTE_DAILY = "mute:daily:"   # mute:daily:{actor_id}:{chat_id}
    _MUTE_TARGET = "mute:target:" # mute:target:{actor_id}:{target_id}:{chat_id}

    async def mute_daily_count(self, actor_id: int, chat_id: int) -> int:
        """Сколько мутов выдано сегодня данным актором."""
        key = f"{self._MUTE_DAILY}{actor_id}:{chat_id}"
        raw = await self._r.get(key)
        return int(raw or 0)

    async def mute_daily_increment(self, actor_id: int, chat_id: int) -> None:
        """Записать ещё один мут. TTL — 24 часа."""
        key = f"{self._MUTE_DAILY}{actor_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400)
        await pipe.execute()

    async def mute_target_cooldown_ok(self, actor_id: int, target_id: int, chat_id: int) -> bool:
        """True если кулдаун прошёл (можно снова мутить этого участника)."""
        key = f"{self._MUTE_TARGET}{actor_id}:{target_id}:{chat_id}"
        return not bool(await self._r.exists(key))

    async def mute_target_cooldown_set(self, actor_id: int, target_id: int, chat_id: int, hours: int) -> None:
        """Установить кулдаун между мутами одного участника."""
        key = f"{self._MUTE_TARGET}{actor_id}:{target_id}:{chat_id}"
        await self._r.set(key, "1", ex=hours * 3600)

    # ── /renew: сброс игровых лимитов ────────────────────────────

    _RENEW_DAILY = "renew:daily:"  # renew:daily:{user_id}:{chat_id}

    async def renew_daily_count(self, user_id: int, chat_id: int) -> int:
        """Сколько раз сегодня пользователь уже использовал /renew."""
        key = f"{self._RENEW_DAILY}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        return int(raw or 0)

    async def renew_daily_increment(self, user_id: int, chat_id: int) -> None:
        """Записать использование /renew. TTL — 24 часа."""
        key = f"{self._RENEW_DAILY}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 86400)
        await pipe.execute()

    async def renew_game_limits(self, user_id: int, chat_id: int) -> None:
        """Сбросить все игровые лимиты пользователя (слоты и блекджек)."""
        await self._r.delete(
            f"{_SLOTS_LAST}{user_id}:{chat_id}",
            f"{_SLOTS_DAILY}{user_id}:{chat_id}",
            f"{_BJ_HISTORY}{user_id}:{chat_id}",
        )

    # ── Slots: прогрессивный джекпот ─────────────────────────────

    async def jackpot_add(self, chat_id: int, amount: int) -> None:
        key = f"{_JACKPOT}{chat_id}"
        await self._r.incrby(key, amount)

    async def jackpot_pop(self, chat_id: int) -> int:
        """Забрать весь джекпот. Возвращает сумму."""
        key = f"{_JACKPOT}{chat_id}"
        pipe = self._r.pipeline()
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()
        return int(results[0] or 0)

    async def jackpot_get(self, chat_id: int) -> int:
        key = f"{_JACKPOT}{chat_id}"
        raw = await self._r.get(key)
        return int(raw or 0)

    # ── Мут-гивэвей ──────────────────────────────────────────────
    # Ключ: mutegiveaway:{chat_id}:{roulette_id}
    # Несколько рулеток в одном чате поддерживается.

    _MUTE_ROULETTE = "mutegiveaway:"

    def _mg_key(self, chat_id: int, roulette_id: str) -> str:
        return f"{self._MUTE_ROULETTE}{chat_id}:{roulette_id}"

    async def mute_roulette_create(
        self,
        chat_id: int,
        creator_id: int,
        mute_minutes: int,
        losers_count: int,
        ends_at: float,
    ) -> str:
        """Создать рулетку. Возвращает уникальный roulette_id."""
        import random as _random

        roulette_id = str(_random.randint(10000, 99999))
        key = self._mg_key(chat_id, roulette_id)
        data = json.dumps(
            {
                "roulette_id": roulette_id,
                "creator_id": creator_id,
                "mute_minutes": mute_minutes,
                "losers_count": losers_count,
                "ends_at": ends_at,
                "participants": [],
                "message_id": 0,
            }
        )
        ttl = int(ends_at - time.time()) + 300
        await self._r.set(key, data, ex=max(ttl, 60))
        return roulette_id

    async def mute_roulette_list(self, chat_id: int) -> list[tuple[str, dict]]:
        """Вернуть все активные рулетки в чате: [(roulette_id, data), ...]."""
        results = []
        async for key in self._r.scan_iter(f"{self._MUTE_ROULETTE}{chat_id}:*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            roulette_id = key.split(":")[-1]
            results.append((roulette_id, data))
        return results

    async def mute_roulette_get(self, chat_id: int, roulette_id: str) -> dict | None:
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def mute_roulette_join(self, chat_id: int, roulette_id: str, user_id: int) -> bool:
        """Добавить участника. Возвращает False если уже участвует или нет рулетки."""
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        if raw is None:
            return False
        data = json.loads(raw)
        if user_id in data["participants"]:
            return False
        data["participants"].append(user_id)
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 60))
        return True

    async def mute_roulette_delete(self, chat_id: int, roulette_id: str) -> dict | None:
        """Завершить рулетку. Возвращает данные или None."""
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        await self._r.delete(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def mute_roulette_set_message_id(self, chat_id: int, roulette_id: str, message_id: int) -> None:
        """Сохранить message_id лобби-сообщения рулетки."""
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["message_id"] = message_id
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 60))

    async def mute_roulette_count(self, chat_id: int, roulette_id: str) -> int:
        key = self._mg_key(chat_id, roulette_id)
        raw = await self._r.get(key)
        if raw is None:
            return 0
        return len(json.loads(raw)["participants"])
