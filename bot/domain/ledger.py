"""Журнал операций с баллами: тип каждой операции (ledger.kind)."""

from __future__ import annotations

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
