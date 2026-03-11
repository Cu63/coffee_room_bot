"""Примеры кастомных функций для слотов.

Подключаются в di.py при создании SlotsConfig.
"""

from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta

from bot.application.slots_service import SlotsConfig, SpinResult, SpinOutcome

# ── Пример 1: лимит кручений в день ──────────────────────────────

_daily_spins: dict[tuple[int, int], tuple[date, int]] = defaultdict(
    lambda: (date.min, 0)
)
MAX_DAILY_SPINS = 20


_last_spin: dict[tuple[int, int], datetime] = {}
COOLDOWN = timedelta(hours=1)


def guard_daily_limit(user_id: int, chat_id: int, bet: int) -> bool:
    key = (user_id, chat_id)
    last_date, count = _daily_spins[key]
    today = date.today()
    if last_date != today:
        _daily_spins[key] = (today, 1)
        return True
    if count >= MAX_DAILY_SPINS:
        return False
    _daily_spins[key] = (today, count + 1)
    return True


def guard_cooldown(user_id: int, chat_id: int, bet: int) -> bool:
    key = (user_id, chat_id)
    now = datetime.now(TZ_MSK)
    last = _last_spin.get(key)
    if last is not None and now - last < COOLDOWN:
        return False
    _last_spin[key] = now
    return True


# ── Пример 2: множитель выигрыша в "счастливый час" ──────────────

from datetime import datetime
from bot.domain.tz import TZ_MSK

LUCKY_HOUR_START = 20  # 20:00
LUCKY_HOUR_END   = 21  # 21:00
LUCKY_MULTIPLIER = 1.5


def modifier_lucky_hour(
    user_id: int, chat_id: int, delta: int, result: SpinResult,
) -> int:
    if delta <= 0:
        return delta  # проигрыш не усиливаем
    now = datetime.now(TZ_MSK)
    if LUCKY_HOUR_START <= now.hour < LUCKY_HOUR_END:
        return int(delta * LUCKY_MULTIPLIER)
    return delta


# ── Пример 3: прогрессивный джекпот ──────────────────────────────
# Часть каждой ставки идёт в банк, при джекпоте весь банк забирается

_jackpot_pool: dict[int, int] = defaultdict(int)  # chat_id -> накопленный банк
JACKPOT_CONTRIBUTION = 0.05  # 5% от каждой ставки идёт в банк


def modifier_progressive_jackpot(
    user_id: int, chat_id: int, delta: int, result: SpinResult,
) -> int:
    bet = result.bet
    contribution = int(bet * JACKPOT_CONTRIBUTION)
    _jackpot_pool[chat_id] += contribution

    if result.outcome == SpinOutcome.JACKPOT:
        pool = _jackpot_pool.pop(chat_id, 0)
        return delta + pool  # победитель забирает весь банк

    return delta


# ── Подключение к конфигу ─────────────────────────────────────────

def apply_custom_functions(cfg: SlotsConfig) -> None:
    """Вызвать в di.py при создании SlotsConfig."""
    cfg.register_guard(guard_cooldown)
    cfg.register_guard(guard_daily_limit)
    cfg.register_modifier(modifier_lucky_hour)
    cfg.register_modifier(modifier_progressive_jackpot)