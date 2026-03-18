"""Redis-backed хранилище для временных данных (игры, лимиты, джекпот)."""

from __future__ import annotations

import json
import logging
import time

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Префиксы ключей
_SLOTS_DAILY = "slots:daily:"  # slots:daily:{user_id}:{chat_id}
_SLOTS_LAST = "slots:last:"  # slots:last:{user_id}:{chat_id}
_JACKPOT = "slots:jackpot:"  # slots:jackpot:{chat_id}
_SLOTS_ALL_DAILY = "slots:all:"   # slots:all:{user_id}:{chat_id}  (1 раз в сутки)
_OWNER_MUTE = "owner_mute:"  # owner_mute:{chat_id}:{user_id}
_GIVEAWAY_PERIOD = "giveaway_period:"  # giveaway_period:{chat_id}:{gp_id}
_BURST_WINDOW = "burst:win:"  # burst:win:{user_id}:{chat_id} (sorted set)
_BURST_COOLDOWN = "burst:cd:"  # burst:cd:{user_id}:{chat_id}
_SPARK_ACTIVE = "spark:act:"  # spark:act:{chat_id} (set of user_ids)
_SPARK_RESP = "spark:resp:"   # spark:resp:{user_id}:{chat_id} (set of responder_ids)
_SPARK_COOLDOWN = "spark:cd:" # spark:cd:{user_id}:{chat_id}
_CHAIN_COUNT = "chain:cnt:"   # chain:cnt:{chat_id}:{min_id}:{max_id}
_CHAIN_LAST = "chain:last:"   # chain:last:{chat_id}:{min_id}:{max_id}
_CHAIN_COOLDOWN = "chain:cd:" # chain:cd:{chat_id}:{min_id}:{max_id}
_GAMEBAN = "gameban:"         # gameban:{user_id}:{chat_id} → unix-timestamp окончания


class RedisStore:
    """Обёртка над Redis для хранения игрового состояния."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

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

    # ── Slots: all-ставка (1 раз в сутки) ───────────────────────

    async def slots_all_used_today(self, user_id: int, chat_id: int) -> bool:
        """True если all-ставка уже использована сегодня."""
        key = f"{_SLOTS_ALL_DAILY}{user_id}:{chat_id}"
        return bool(await self._r.exists(key))

    async def slots_all_mark_used(self, user_id: int, chat_id: int) -> None:
        """Отметить использование all-ставки. TTL 24 часа."""
        key = f"{_SLOTS_ALL_DAILY}{user_id}:{chat_id}"
        await self._r.set(key, "1", ex=86400)

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
        """Сбросить все игровые лимиты пользователя (слоты)."""
        await self._r.delete(
            f"{_SLOTS_LAST}{user_id}:{chat_id}",
            f"{_SLOTS_DAILY}{user_id}:{chat_id}",
            f"{_SLOTS_ALL_DAILY}{user_id}:{chat_id}",
        )

    # ── Самозапрет на игры ───────────────────────────────────────

    async def gameban_set(self, user_id: int, chat_id: int, until_ts: float) -> None:
        """Установить самозапрет на игры до until_ts (unix timestamp)."""
        key = f"{_GAMEBAN}{user_id}:{chat_id}"
        ttl = max(int(until_ts - time.time()) + 10, 60)
        await self._r.set(key, str(until_ts), ex=ttl)

    async def gameban_active(self, user_id: int, chat_id: int) -> bool:
        """Проверить, активен ли самозапрет на игры."""
        key = f"{_GAMEBAN}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return False
        return float(raw) > time.time()

    async def gameban_get_until(self, user_id: int, chat_id: int) -> float | None:
        """Получить unix timestamp окончания запрета. None если нет."""
        key = f"{_GAMEBAN}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return None
        ts = float(raw)
        return ts if ts > time.time() else None

    async def gameban_delete(self, user_id: int, chat_id: int) -> None:
        """Снять самозапрет досрочно."""
        key = f"{_GAMEBAN}{user_id}:{chat_id}"
        await self._r.delete(key)

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

    # ── Burst: скользящее окно сообщений + кулдаун ─────────────

    async def burst_cooldown_active(self, user_id: int, chat_id: int) -> bool:
        """True если кулдаун активен (награда недавно выдана)."""
        key = f"{_BURST_COOLDOWN}{user_id}:{chat_id}"
        return bool(await self._r.exists(key))

    async def burst_add_message(
        self,
        user_id: int,
        chat_id: int,
        window_seconds: int,
    ) -> int:
        """Добавить timestamp в скользящее окно. Возвращает кол-во сообщений в окне."""
        key = f"{_BURST_WINDOW}{user_id}:{chat_id}"
        now = time.time()
        cutoff = now - window_seconds

        pipe = self._r.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 60)
        results = await pipe.execute()
        return results[2]  # zcard result

    async def burst_set_cooldown(self, user_id: int, chat_id: int, cooldown_seconds: int) -> None:
        """Поставить кулдаун и очистить окно."""
        cd_key = f"{_BURST_COOLDOWN}{user_id}:{chat_id}"
        win_key = f"{_BURST_WINDOW}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.set(cd_key, "1", ex=cooldown_seconds)
        pipe.delete(win_key)
        await pipe.execute()

    # ── Spark: разжигатель дискуссии ─────────────────────────────

    async def spark_cooldown_active(self, user_id: int, chat_id: int) -> bool:
        key = f"{_SPARK_COOLDOWN}{user_id}:{chat_id}"
        return bool(await self._r.exists(key))

    async def spark_activate(self, user_id: int, chat_id: int, window_seconds: int) -> None:
        """Зарегистрировать пользователя как потенциального зачинщика."""
        active_key = f"{_SPARK_ACTIVE}{chat_id}"
        resp_key = f"{_SPARK_RESP}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.sadd(active_key, str(user_id))
        pipe.expire(active_key, window_seconds + 60)
        pipe.delete(resp_key)  # сбросить старых респондентов
        pipe.expire(resp_key, window_seconds)
        await pipe.execute()

    async def spark_get_active(self, chat_id: int) -> set[int]:
        """Все активные зачинщики в чате."""
        key = f"{_SPARK_ACTIVE}{chat_id}"
        members = await self._r.smembers(key)
        return {int(m) for m in members}

    async def spark_add_responder(self, anchor_id: int, responder_id: int, chat_id: int) -> int:
        """Добавить респондента к зачинщику. Возвращает кол-во уникальных респондентов."""
        key = f"{_SPARK_RESP}{anchor_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.sadd(key, str(responder_id))
        pipe.scard(key)
        results = await pipe.execute()
        return results[1]

    async def spark_award_cleanup(self, user_id: int, chat_id: int, cooldown_seconds: int) -> None:
        """Начислить награду: поставить кулдаун, убрать из активных, удалить респондентов."""
        cd_key = f"{_SPARK_COOLDOWN}{user_id}:{chat_id}"
        active_key = f"{_SPARK_ACTIVE}{chat_id}"
        resp_key = f"{_SPARK_RESP}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.set(cd_key, "1", ex=cooldown_seconds)
        pipe.srem(active_key, str(user_id))
        pipe.delete(resp_key)
        await pipe.execute()

    # ── Reply chain: цепочка реплаев ──────────────────────────────

    def _chain_pair(self, chat_id: int, user_a: int, user_b: int) -> str:
        lo, hi = min(user_a, user_b), max(user_a, user_b)
        return f"{chat_id}:{lo}:{hi}"

    async def chain_cooldown_active(self, chat_id: int, user_a: int, user_b: int) -> bool:
        pair = self._chain_pair(chat_id, user_a, user_b)
        return bool(await self._r.exists(f"{_CHAIN_COOLDOWN}{pair}"))

    async def chain_add_reply(
        self, chat_id: int, replier_id: int, author_id: int, window_seconds: int,
    ) -> int | None:
        """Добавить реплай в цепочку. Возвращает счётчик или None если не чередуется."""
        pair = self._chain_pair(chat_id, replier_id, author_id)
        last_key = f"{_CHAIN_LAST}{pair}"
        cnt_key = f"{_CHAIN_COUNT}{pair}"

        last = await self._r.get(last_key)
        if last is not None and int(last) == replier_id:
            return None  # тот же человек подряд — не считаем

        pipe = self._r.pipeline()
        pipe.set(last_key, str(replier_id), ex=window_seconds)
        pipe.incr(cnt_key)
        pipe.expire(cnt_key, window_seconds)
        results = await pipe.execute()
        return results[1]  # новое значение счётчика

    async def chain_award_cleanup(
        self, chat_id: int, user_a: int, user_b: int, cooldown_seconds: int,
    ) -> None:
        """Поставить кулдаун на пару и сбросить цепочку."""
        pair = self._chain_pair(chat_id, user_a, user_b)
        pipe = self._r.pipeline()
        pipe.set(f"{_CHAIN_COOLDOWN}{pair}", "1", ex=cooldown_seconds)
        pipe.delete(f"{_CHAIN_COUNT}{pair}", f"{_CHAIN_LAST}{pair}")
        await pipe.execute()

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

    # ── Owner mute (soft-mute: удаление сообщений) ───────────────
    # Используется когда цель мута — владелец чата (ChatMemberOwner),
    # которого нельзя ограничить через Telegram API.
    # Ключ: owner_mute:{chat_id}:{user_id}  →  unix-timestamp окончания

    async def owner_mute_set(self, chat_id: int, user_id: int, until_ts: float) -> None:
        """Установить owner-мут до until_ts (unix timestamp)."""
        key = f"{_OWNER_MUTE}{chat_id}:{user_id}"
        ttl = max(int(until_ts - time.time()) + 10, 60)
        await self._r.set(key, str(until_ts), ex=ttl)

    async def owner_mute_active(self, chat_id: int, user_id: int) -> bool:
        """Проверить, активен ли owner-мут прямо сейчас."""
        key = f"{_OWNER_MUTE}{chat_id}:{user_id}"
        raw = await self._r.get(key)
        if raw is None:
            return False
        return float(raw) > time.time()

    async def owner_mute_get_ts(self, chat_id: int, user_id: int) -> float | None:
        """Получить unix timestamp окончания owner-мута. None если нет."""
        key = f"{_OWNER_MUTE}{chat_id}:{user_id}"
        raw = await self._r.get(key)
        return float(raw) if raw else None

    async def owner_mute_delete(self, chat_id: int, user_id: int) -> None:
        """Снять owner-мут досрочно."""
        key = f"{_OWNER_MUTE}{chat_id}:{user_id}"
        await self._r.delete(key)

    # ── Периодические гивэвеи ─────────────────────────────────────
    # Ключ: giveaway_period:{chat_id}:{gp_id}
    # TTL не ставится — запись живёт до явного удаления командой stop.

    def _gp_key(self, chat_id: int, gp_id: str) -> str:
        return f"{_GIVEAWAY_PERIOD}{chat_id}:{gp_id}"

    async def giveaway_period_create(
        self,
        chat_id: int,
        created_by: int,
        prizes: list[int],
        period_seconds: int,
        round_duration_seconds: int | None,
    ) -> str:
        """Создать периодический гивэвей. Возвращает gp_id."""
        import random as _random

        gp_id = str(_random.randint(10000, 99999))
        key = self._gp_key(chat_id, gp_id)
        data = json.dumps(
            {
                "gp_id": gp_id,
                "chat_id": chat_id,
                "created_by": created_by,
                "prizes": prizes,
                "period_seconds": period_seconds,
                "round_duration_seconds": round_duration_seconds,
                "next_run": time.time(),  # запустить немедленно
            }
        )
        await self._r.set(key, data)  # без TTL — живёт до /giveaway_period_stop
        return gp_id

    async def giveaway_period_list(self, chat_id: int) -> list[tuple[str, dict]]:
        """Все активные периодические гивэвеи в чате."""
        results = []
        async for key in self._r.scan_iter(f"{_GIVEAWAY_PERIOD}{chat_id}:*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            results.append((data["gp_id"], data))
        return results

    async def giveaway_period_all(self) -> list[dict]:
        """Все периодические гивэвеи по всем чатам (для фонового луп)."""
        results = []
        async for key in self._r.scan_iter(f"{_GIVEAWAY_PERIOD}*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            results.append(json.loads(raw))
        return results

    async def giveaway_period_update_next_run(self, chat_id: int, gp_id: str, next_run: float) -> None:
        """Обновить время следующего запуска."""
        key = self._gp_key(chat_id, gp_id)
        raw = await self._r.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["next_run"] = next_run
        await self._r.set(key, json.dumps(data))

    async def giveaway_period_delete(self, chat_id: int, gp_id: str) -> dict | None:
        """Удалить периодический гивэвей. Возвращает данные или None."""
        key = self._gp_key(chat_id, gp_id)
        raw = await self._r.get(key)
        await self._r.delete(key)
        if raw is None:
            return None
        return json.loads(raw)

    # ── Угадайка ──────────────────────────────────────────────────────────────
    # wg:pending:{game_id}  → pending-запись (ставка, чат, время) TTL 10 мин
    # wg:awaiting:{user_id} → game_id ожидаемой игры TTL 10 мин
    # wg:game:{game_id}     → полное состояние активной игры
    # wg:chat:{chat_id}     → SET активных game_id в чате

    _WG_PENDING  = "wg:pending:"
    _WG_AWAITING = "wg:awaiting:"
    _WG_GAME     = "wg:game:"
    _WG_CHAT     = "wg:chat:"
    _WG_RATE     = "wg:rate:"
    _WG_TTL      = 600  # 10 минут на ввод слова создателем

    async def wg_pending_create(
        self, user_id: int, chat_id: int, bet: int, duration_seconds: int
    ) -> str:
        """Создать pending-запись. Возвращает game_id."""
        import random as _r
        game_id = str(_r.randint(10000, 99999))
        data = json.dumps({
            "game_id": game_id, "user_id": user_id, "chat_id": chat_id,
            "bet": bet, "duration_seconds": duration_seconds, "lobby_msg_id": 0,
        })
        await self._r.set(f"{self._WG_PENDING}{game_id}", data, ex=self._WG_TTL)
        return game_id

    async def wg_pending_get(self, game_id: str) -> dict | None:
        raw = await self._r.get(f"{self._WG_PENDING}{game_id}")
        return json.loads(raw) if raw else None

    async def wg_pending_set_lobby_msg(self, game_id: str, message_id: int) -> None:
        key = f"{self._WG_PENDING}{game_id}"
        raw = await self._r.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["lobby_msg_id"] = message_id
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 30))

    async def wg_pending_delete(self, game_id: str) -> None:
        await self._r.delete(f"{self._WG_PENDING}{game_id}")

    async def wg_awaiting_set(self, user_id: int, game_id: str) -> None:
        await self._r.set(f"{self._WG_AWAITING}{user_id}", game_id, ex=self._WG_TTL)

    async def wg_awaiting_get(self, user_id: int) -> str | None:
        return await self._r.get(f"{self._WG_AWAITING}{user_id}")

    async def wg_awaiting_delete(self, user_id: int) -> None:
        await self._r.delete(f"{self._WG_AWAITING}{user_id}")

    async def wg_rate_check(self, user_id: int, max_games: int, window_seconds: int) -> int:
        """Проверить сколько игр создал пользователь за окно. Не инкрементирует."""
        key = f"{self._WG_RATE}{user_id}"
        now = time.time()
        await self._r.zremrangebyscore(key, 0, now - window_seconds)
        return await self._r.zcard(key)

    async def wg_rate_record(self, user_id: int, window_seconds: int) -> None:
        """Записать факт создания игры."""
        key = f"{self._WG_RATE}{user_id}"
        now = time.time()
        await self._r.zadd(key, {str(now): now})
        await self._r.expire(key, window_seconds)

    async def wg_rate_check_rword(self, user_id: int, max_games: int, window_seconds: int) -> int:
        """Проверить лимит /rword отдельно от /word."""
        key = f"{self._WG_RATE}rword:{user_id}"
        now = time.time()
        await self._r.zremrangebyscore(key, 0, now - window_seconds)
        return await self._r.zcard(key)

    async def wg_rate_record_rword(self, user_id: int, window_seconds: int) -> None:
        """Записать факт создания /rword игры."""
        key = f"{self._WG_RATE}rword:{user_id}"
        now = time.time()
        await self._r.zadd(key, {str(now): now})
        await self._r.expire(key, window_seconds)

    async def wg_game_create(self, game) -> None:
        """Сохранить новую активную игру (WordGame)."""
        key = f"{self._WG_GAME}{game.game_id}"
        data = {
            "game_id": game.game_id, "chat_id": game.chat_id,
            "creator_id": game.creator_id, "word": game.word,
            "bet": game.bet, "ends_at": game.ends_at,
            "revealed": game.revealed, "guesses": game.guesses,
            "message_id": game.message_id,
            "finished": game.finished, "winner_id": game.winner_id,
            "is_random": game.is_random,
        }
        ttl = int(game.ends_at - time.time()) + 120
        await self._r.set(key, json.dumps(data), ex=max(ttl, 60))

    async def wg_game_get(self, game_id: str) -> dict | None:
        raw = await self._r.get(f"{self._WG_GAME}{game_id}")
        return json.loads(raw) if raw else None

    async def wg_game_save_raw(self, game_id: str, data: dict) -> None:
        key = f"{self._WG_GAME}{game_id}"
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 30))

    async def wg_game_finish(self, game_id: str) -> dict | None:
        """Атомарно завершить игру. Возвращает данные или None если уже завершена."""
        key = f"{self._WG_GAME}{game_id}"
        raw = await self._r.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        if data.get("finished"):
            return None
        data["finished"] = True
        await self._r.delete(key)
        return data

    async def wg_chat_add(self, chat_id: int, game_id: str) -> None:
        await self._r.sadd(f"{self._WG_CHAT}{chat_id}", game_id)

    async def wg_chat_remove(self, chat_id: int, game_id: str) -> None:
        await self._r.srem(f"{self._WG_CHAT}{chat_id}", game_id)

    async def wg_chat_games(self, chat_id: int) -> list[str]:
        """Активные game_id в чате; ленивая очистка устаревших."""
        members = await self._r.smembers(f"{self._WG_CHAT}{chat_id}")
        active = []
        for gid in members:
            if await self._r.exists(f"{self._WG_GAME}{gid}"):
                active.append(gid)
            else:
                await self._r.srem(f"{self._WG_CHAT}{chat_id}", gid)
        return active

    async def wg_game_by_message_id(self, chat_id: int, message_id: int) -> dict | None:
        """Найти активную игру в чате по message_id игрового сообщения."""
        members = await self._r.smembers(f"{self._WG_CHAT}{chat_id}")
        for gid in members:
            raw = await self._r.get(f"{self._WG_GAME}{gid}")
            if raw is None:
                await self._r.srem(f"{self._WG_CHAT}{chat_id}", gid)
                continue
            data = json.loads(raw)
            if data.get("message_id") == message_id:
                return data
        return None
    # ── Трекер: привязка чатов ────────────────────────────────────
    # ── Анонимные сообщения ───────────────────────────────────────
    # anon:chats                         → hash {chat_id: title}
    # anon:state:{user_id}               → JSON, TTL 300s

    async def anon_register_chat(self, chat_id: int, title: str) -> None:
        """Зарегистрировать чат как доступный для анонимных сообщений."""
        await self._r.hset("anon:chats", str(chat_id), title)

    async def anon_get_chats(self) -> list[tuple[int, str]]:
        """Вернуть список (chat_id, title) всех зарегистрированных чатов."""
        raw = await self._r.hgetall("anon:chats")
        return [(int(k), v) for k, v in raw.items()]

    async def anon_get_state(self, user_id: int) -> dict | None:
        """Получить FSM-состояние пользователя для /anon. None если нет."""
        raw = await self._r.get(f"anon:state:{user_id}")
        return json.loads(raw) if raw else None

    async def anon_set_state(self, user_id: int, state: dict, ttl: int = 300) -> None:
        """Сохранить FSM-состояние пользователя для /anon."""
        await self._r.set(f"anon:state:{user_id}", json.dumps(state, ensure_ascii=False), ex=ttl)

    async def anon_clear_state(self, user_id: int) -> None:
        """Очистить FSM-состояние пользователя для /anon."""
        await self._r.delete(f"anon:state:{user_id}")

    # tracker:source:{source_chat_id}        → str(tracker_chat_id)
    # tracker:topic:{tracker_chat_id}:{type} → str(thread_id)
    # tracker:counter                        → глобальный счётчик репортов

    async def tracker_set_source(self, source_chat_id: int, tracker_chat_id: int) -> None:
        """Привязать основной чат к трекер-чату."""
        await self._r.set(f"tracker:source:{source_chat_id}", str(tracker_chat_id))

    async def tracker_get_tracker_id(self, source_chat_id: int) -> int | None:
        """Получить tracker_chat_id для source_chat_id. None если не привязан."""
        raw = await self._r.get(f"tracker:source:{source_chat_id}")
        return int(raw) if raw else None

    async def tracker_set_topic(self, tracker_chat_id: int, topic_type: str, thread_id: int) -> None:
        """Назначить топик для типа обращения (bug/feature/report/changelog)."""
        await self._r.set(f"tracker:topic:{tracker_chat_id}:{topic_type}", str(thread_id))

    async def tracker_get_topic(self, tracker_chat_id: int, topic_type: str) -> int | None:
        """Получить thread_id топика по типу. None если не назначен."""
        raw = await self._r.get(f"tracker:topic:{tracker_chat_id}:{topic_type}")
        return int(raw) if raw else None

    async def tracker_next_id(self) -> int:
        """Атомарно выдать следующий глобальный номер репорта."""
        return int(await self._r.incr("tracker:counter"))

    # ── Changelog ─────────────────────────────────────────────────
    # changelog:{tracker_chat_id} → JSON-список, последние 20 записей
    # Каждая запись: {message_id, text, date}
    # Порядок: новые впереди.

    _CHANGELOG_MAX = 20

    async def changelog_add(self, tracker_chat_id: int, message_id: int, text: str, date: str) -> None:
        """Добавить/обновить запись ченджлога. Если message_id уже есть — обновить."""
        key = f"changelog:{tracker_chat_id}"
        raw = await self._r.get(key)
        entries: list[dict] = json.loads(raw) if raw else []

        # Убрать старую запись с тем же message_id (при редактировании)
        entries = [e for e in entries if e["message_id"] != message_id]

        # Добавить новую запись в начало
        entries.insert(0, {"message_id": message_id, "text": text, "date": date})

        # Ограничить размер
        entries = entries[: self._CHANGELOG_MAX]
        await self._r.set(key, json.dumps(entries, ensure_ascii=False))

    async def changelog_remove(self, tracker_chat_id: int, message_id: int) -> None:
        """Удалить запись ченджлога по message_id."""
        key = f"changelog:{tracker_chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return
        entries = [e for e in json.loads(raw) if e["message_id"] != message_id]
        await self._r.set(key, json.dumps(entries, ensure_ascii=False))

    async def changelog_get_all(self, tracker_chat_id: int) -> list[dict]:
        """Все записи ченджлога (новые впереди)."""
        raw = await self._r.get(f"changelog:{tracker_chat_id}")
        return json.loads(raw) if raw else []

    async def changelog_scan_all(self) -> list[tuple[int, list[dict]]]:
        """Все ченджлоги по всем трекер-чатам. Используется для лога при старте."""
        result: list[tuple[int, list[dict]]] = []
        async for key in self._r.scan_iter("changelog:*"):
            raw = await self._r.get(key)
            if not raw:
                continue
            tracker_chat_id = int(key.split(":", 1)[1])
            entries = json.loads(raw)
            if entries:
                result.append((tracker_chat_id, entries))
        return result