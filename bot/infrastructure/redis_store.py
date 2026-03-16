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
_OWNER_MUTE = "owner_mute:"  # owner_mute:{chat_id}:{user_id}
_GIVEAWAY_PERIOD = "giveaway_period:"  # giveaway_period:{chat_id}:{gp_id}


def _serialize_round(
    rnd: BlackjackRound,
    *,
    message_id: int = 0,
    expires_at: float = 0.0,
) -> str:
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
        "message_id": message_id,
        "expires_at": expires_at,
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

    async def bj_set(
        self,
        user_id: int,
        chat_id: int,
        rnd: BlackjackRound,
        *,
        message_id: int = 0,
        timeout_seconds: int = 60,
    ) -> None:
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        expires_at = time.time() + timeout_seconds
        raw = _serialize_round(rnd, message_id=message_id, expires_at=expires_at)
        await self._r.set(key, raw, ex=timeout_seconds + 30)  # +30с буфер для cleanup

    async def bj_set_message_id(self, user_id: int, chat_id: int, message_id: int) -> None:
        """Обновить message_id игрового сообщения (вызывать после отправки)."""
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        raw = await self._r.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["message_id"] = message_id
        ttl = await self._r.ttl(key)
        await self._r.set(key, json.dumps(data), ex=max(ttl, 10))

    async def bj_delete(self, user_id: int, chat_id: int) -> None:
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        await self._r.delete(key)

    async def bj_pop_expired(self) -> list[dict]:
        """Найти и атомарно удалить все истёкшие игры. Возвращает их данные."""
        now = time.time()
        expired: list[dict] = []
        async for key in self._r.scan_iter(f"{_BJ_GAME}*"):
            raw = await self._r.get(key)
            if raw is None:
                continue
            data = json.loads(raw)
            if data.get("expires_at", 0.0) <= now:
                # DEL возвращает кол-во удалённых ключей; 0 = уже удалён другим процессом
                if await self._r.delete(key):
                    expired.append(data)
        return expired

    async def bj_exists(self, user_id: int, chat_id: int) -> bool:
        key = f"{_BJ_GAME}{user_id}:{chat_id}"
        return bool(await self._r.exists(key))

    # ── Blackjack: лимит игр (fixed window) ─────────────────────

    async def bj_check_start(
        self,
        user_id: int,
        chat_id: int,
        max_games: int,
    ) -> tuple[bool, float | None]:
        """Одним pipeline проверить активную игру и лимит окна.

        Returns:
            (has_active_game, wait_seconds)
            wait_seconds = None если можно играть, иначе секунд до сброса окна.
        """
        game_key = f"{_BJ_GAME}{user_id}:{chat_id}"
        hist_key = f"{_BJ_HISTORY}{user_id}:{chat_id}"
        pipe = self._r.pipeline()
        pipe.exists(game_key)
        pipe.get(hist_key)
        pipe.ttl(hist_key)
        game_exists, count_raw, ttl = await pipe.execute()
        has_active = bool(game_exists)
        if count_raw is None or int(count_raw) < max_games:
            return has_active, None
        return has_active, max(int(ttl), 0)

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