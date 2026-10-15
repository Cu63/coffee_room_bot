"""Журнал операций с баллами: тип каждой операции (ledger.kind)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LedgerKind(StrEnum):
    """Что изменило баланс. Обе стороны одной операции пишутся с одним типом."""

    REACTION = "reaction"  # реакция на сообщение и её снятие
    TRANSFER = "transfer"  # /transfer
    PURCHASE = "purchase"  # платное действие: покупатель → касса бота
    STAKE = "stake"  # ставка в игре (в банк игры или в кассу)
    PRIZE = "prize"  # выигрыш
    REFUND = "refund"  # возврат оплаты или ставки
    BONUS = "bonus"  # награды за активность, розыгрыши, лидер дня
    PENALTY = "penalty"  # игровой штраф: баллы забирает касса или другой игрок (/poker)
    TAX = "tax"  # ежедневный налог: плательщик → касса
    ADMIN = "admin"  # ручная правка админом
    OPENING = "opening"  # остаток на момент появления журнала


# Типы, у которых вторая сторона — инициатор: кто поставил реакцию, какой админ правил баланс
_INITIATED_BY_COUNTERPARTY = {LedgerKind.REACTION, LedgerKind.ADMIN}


@dataclass(slots=True, frozen=True)
class LedgerEntry:
    """Строка журнала операций: изменение баланса user_id в chat_id."""

    chat_id: int
    user_id: int
    delta: int
    kind: str  # LedgerKind или 'unknown' — правка мимо кода
    ref: str | None
    counterparty_id: int | None
    created_at: datetime

    @property
    def actor_id(self) -> int:
        """Кто → … в истории: инициатор, а для денег — тот, от кого они ушли."""
        if self.kind in _INITIATED_BY_COUNTERPARTY or self.delta > 0:
            return self.counterparty_id or self.user_id
        return self.user_id

    @property
    def target_id(self) -> int:
        """… → кому в истории: на ком операция, а для денег — к кому они пришли."""
        if self.kind in _INITIATED_BY_COUNTERPARTY or self.delta > 0:
            return self.user_id
        return self.counterparty_id or self.user_id

    @property
    def reaction_emoji(self) -> str | None:
        """Эмодзи реакции из ref вида msg:<id>:<эмодзи>."""
        if self.kind != LedgerKind.REACTION or not self.ref or self.ref.count(":") < 2:
            return None
        return self.ref.split(":", 2)[2]
