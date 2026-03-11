"""Сервис слотов.

Архитектура:
- SlotsConfig      — настройки (символы, ставки, RTP, кастомные функции)
- SlotsMachine     — логика барабанов и подсчёта выигрыша
- SlotsService     — оркестрация: крутим, считаем, списываем/начисляем баллы

RTP (Return to Player) — процент возврата от всех ставок игрокам.
Управляется через веса символов + множитель rtp_bias:
  rtp_bias = 1.0  → честная игра (только веса символов)
  rtp_bias < 1.0  → казино в плюсе
  rtp_bias > 1.0  → игроки в плюсе
"""

from __future__ import annotations

from datetime import timedelta
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


# ── Типы кастомных функций ────────────────────────────────────────

# Функция-фильтр: можно ли вообще крутить (антиспам, особые условия)
# Принимает user_id, chat_id, ставку → True если разрешено
SpinGuard = Callable[[int, int, int], bool]

# Функция-модификатор выигрыша: можно изменить итоговый delta перед начислением
# Принимает user_id, chat_id, delta, результат → новый delta
PayoutModifier = Callable[[int, int, int, "SpinResult"], int]


# ── Конфиг ───────────────────────────────────────────────────────

@dataclass
class SlotSymbol:
    emoji: str
    weight: int        # вероятностный вес (чем больше — тем чаще выпадает)
    multiplier: float  # множитель ставки при 3 одинаковых


@dataclass
class SlotsConfig:
    min_bet: int = 1
    max_bet: int = 200
    reels: int = 3          # количество барабанов
    rtp_bias: float = 0.95  # 0.95 = 95% RTP

    symbols: list[SlotSymbol] = field(default_factory=lambda: [
        SlotSymbol("🍒", weight=40, multiplier=2.0),
        SlotSymbol("🍋", weight=30, multiplier=3.0),
        SlotSymbol("🍊", weight=20, multiplier=5.0),
        SlotSymbol("⭐", weight=8,  multiplier=10.0),
        SlotSymbol("💎", weight=2,  multiplier=50.0),
    ])

    # Кастомные функции (подключаются снаружи)
    spin_guards: list[SpinGuard] = field(default_factory=list)
    payout_modifiers: list[PayoutModifier] = field(default_factory=list)

    def register_guard(self, fn: SpinGuard) -> SpinGuard:
        """Декоратор для регистрации guard-функции."""
        self.spin_guards.append(fn)
        return fn

    def register_modifier(self, fn: PayoutModifier) -> PayoutModifier:
        """Декоратор для регистрации модификатора выплаты."""
        self.payout_modifiers.append(fn)
        return fn


# ── Результат ─────────────────────────────────────────────────────

class SpinOutcome(str, Enum):
    JACKPOT   = "jackpot"    # все одинаковые + топ символ
    WIN       = "win"        # все одинаковые
    NEAR_MISS = "near_miss"  # 2 одинаковых
    LOSS      = "loss"       # ничего


@dataclass
class SpinResult:
    reels: list[str]          # итоговые символы, напр. ["🍒", "🍒", "🍒"]
    outcome: SpinOutcome
    multiplier: float         # итоговый множитель
    bet: int
    delta: int                # изменение баланса (отриц. при проигрыше)
    new_balance: int = 0      # заполняется после начисления


# ── Машина ───────────────────────────────────────────────────────

class SlotsMachine:
    def __init__(self, config: SlotsConfig) -> None:
        self._cfg = config

    def _spin_reel(self) -> SlotSymbol:
        """Крутим один барабан с учётом rtp_bias."""
        symbols = self._cfg.symbols
        # Применяем bias к весам: топовые символы (высокий multiplier) получают
        # сниженный вес при bias < 1, и повышенный при bias > 1
        adjusted_weights = []
        for s in symbols:
            # Чем выше multiplier, тем сильнее bias влияет на вес
            bias_effect = self._cfg.rtp_bias ** (s.multiplier / 10)
            adjusted_weights.append(s.weight * bias_effect)

        return random.choices(symbols, weights=adjusted_weights, k=1)[0]

    def spin(self, bet: int) -> SpinResult:
        """Крутим все барабаны и считаем результат."""
        landed = [self._spin_reel() for _ in range(self._cfg.reels)]
        emojis = [s.emoji for s in landed]

        # Все одинаковые?
        if len(set(emojis)) == 1:
            symbol = landed[0]
            # Джекпот — топ символ (наименьший вес)
            top_symbol = min(self._cfg.symbols, key=lambda s: s.weight)
            is_jackpot = (symbol.emoji == top_symbol.emoji)
            outcome = SpinOutcome.JACKPOT if is_jackpot else SpinOutcome.WIN
            multiplier = symbol.multiplier
            delta = int(bet * multiplier)
        elif len(set(emojis)) == self._cfg.reels - 1:
            # Near miss — почти выиграл (2 из 3 одинаковых)
            outcome = SpinOutcome.NEAR_MISS
            multiplier = 0.0
            delta = -bet
        else:
            outcome = SpinOutcome.LOSS
            multiplier = 0.0
            delta = -bet

        return SpinResult(
            reels=emojis,
            outcome=outcome,
            multiplier=multiplier,
            bet=bet,
            delta=delta,
        )

    @property
    def theoretical_rtp(self) -> float:
        """Считает теоретический RTP на основе весов и rtp_bias."""
        total_weight = sum(s.weight for s in self._cfg.symbols)
        rtp = 0.0
        for s in self._cfg.symbols:
            prob = (s.weight / total_weight) ** self._cfg.reels
            rtp += prob * s.multiplier
        return rtp * self._cfg.rtp_bias


# ── Сервис ────────────────────────────────────────────────────────

class SlotsService:
    def __init__(
        self,
        machine: SlotsMachine,
        config: SlotsConfig,
        score_service,   # ScoreService — не импортируем напрямую чтобы не было цикла
    ) -> None:
        self._machine = machine
        self._cfg = config
        self._score_service = score_service

    async def spin(
        self,
        user_id: int,
        chat_id: int,
        bet: int,
    ) -> SpinResult | str:
        """Крутим слоты.

        Возвращает SpinResult или строку-ошибку если нельзя крутить.
        """
        # Проверка ставки
        if bet < self._cfg.min_bet or bet > self._cfg.max_bet:
            return "invalid_bet"

        # Проверка кастомных guard-функций
        for guard in self._cfg.spin_guards:
            if not guard(user_id, chat_id, bet):
                return "guard_rejected"

        # Проверка баланса
        score = await self._score_service.get_score(user_id, chat_id)
        if score.value < bet:
            return "not_enough"

        # Крутим
        result = self._machine.spin(bet)

        # Применяем кастомные модификаторы выплаты
        for modifier in self._cfg.payout_modifiers:
            result.delta = modifier(user_id, chat_id, result.delta, result)

        # Начисляем/списываем
        from bot.application.score_service import SPECIAL_EMOJI
        new_balance = await self._score_service._score_repo.add_delta(
            user_id, chat_id, result.delta,
        )
        await self._score_service._save_special_event(
            actor_id=user_id,
            target_id=user_id,
            chat_id=chat_id,
            delta=result.delta,
            emoji="🎰",
        )

        result.new_balance = new_balance
        return result
    
        # slots_service.py
    def cooldown_remaining(self, user_id: int, chat_id: int) -> timedelta | None:
        """Возвращает оставшееся время до следующего кручения или None если можно."""
        from bot.application.slots_custom_functions import _last_spin, COOLDOWN
        from datetime import datetime
        from bot.domain.tz import TZ_MSK
        key = (user_id, chat_id)
        last = _last_spin.get(key)
        if last is None:
            return None
        remaining = COOLDOWN - (datetime.now(TZ_MSK) - last)
        return remaining if remaining.total_seconds() > 0 else None