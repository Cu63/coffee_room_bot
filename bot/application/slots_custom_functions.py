"""Кастомные функции для слотов (Redis-backed).

Подключаются в di.py при создании SlotsConfig.
"""

from __future__ import annotations

from bot.application.slots_service import SlotsConfig, SpinResult, SpinOutcome
from bot.infrastructure.redis_store import RedisStore

MAX_DAILY_SPINS = 20
COOLDOWN_SECONDS = 3600  # 1 час
LUCKY_HOUR_START = 20
LUCKY_HOUR_END = 21
LUCKY_MULTIPLIER = 1.5
JACKPOT_CONTRIBUTION = 0.05


def make_guard_daily_limit(store: RedisStore):
    async def guard_daily_limit(user_id: int, chat_id: int, bet: int) -> bool:
        ok = await store.slots_daily_check(user_id, chat_id, MAX_DAILY_SPINS)
        if ok:
            await store.slots_daily_increment(user_id, chat_id)
        return ok
    return guard_daily_limit


def make_guard_cooldown(store: RedisStore):
    async def guard_cooldown(user_id: int, chat_id: int, bet: int) -> bool:
        ok = await store.slots_cooldown_check(user_id, chat_id, COOLDOWN_SECONDS)
        if ok:
            await store.slots_cooldown_set(user_id, chat_id, COOLDOWN_SECONDS)
        return ok
    return guard_cooldown


def make_modifier_lucky_hour():
    async def modifier_lucky_hour(
        user_id: int, chat_id: int, delta: int, result: SpinResult,
    ) -> int:
        if delta <= 0:
            return delta
        from datetime import datetime
        from bot.domain.tz import TZ_MSK
        now = datetime.now(TZ_MSK)
        if LUCKY_HOUR_START <= now.hour < LUCKY_HOUR_END:
            return int(delta * LUCKY_MULTIPLIER)
        return delta
    return modifier_lucky_hour


def make_modifier_progressive_jackpot(store: RedisStore):
    async def modifier_progressive_jackpot(
        user_id: int, chat_id: int, delta: int, result: SpinResult,
    ) -> int:
        bet = result.bet
        contribution = int(bet * JACKPOT_CONTRIBUTION)
        await store.jackpot_add(chat_id, contribution)

        if result.outcome == SpinOutcome.JACKPOT:
            pool = await store.jackpot_pop(chat_id)
            return delta + pool

        return delta
    return modifier_progressive_jackpot


def apply_custom_functions(cfg: SlotsConfig, store: RedisStore) -> None:
    """Вызвать в di.py при создании SlotsConfig."""
    cfg.register_guard(make_guard_cooldown(store))
    cfg.register_guard(make_guard_daily_limit(store))
    cfg.register_modifier(make_modifier_lucky_hour())
    cfg.register_modifier(make_modifier_progressive_jackpot(store))
