"""Фоновая задача: сверка журнала операций с балансами.

Журнал ведёт триггер на scores (V017__ledger.sql), поэтому сумма журнала по паре
(чат, пользователь) обязана совпадать с балансом. Расхождение значит, что журнал
правили вручную или триггер отключали — это ERROR. Изменения без типа операции
(kind = 'unknown') значат правку баланса мимо кода — WARNING.

Заодно проверяются банки пошаговых игр (game_pots): банк с деньгами, который сутки
не трогали, — игра, потерянная без расчёта (например, ключ пропал из Redis). Ставки
из него вернуть может только админ — WARNING со списком. Опустевшие банки старше
недели удаляются: они нужны, только пока игру могут по ошибке рассчитать повторно.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bot.application.interfaces.game_pot_repository import GamePot, IGamePotRepository
from bot.application.interfaces.ledger_repository import ILedgerRepository, LedgerMismatch

logger = logging.getLogger(__name__)

_FIRST_RUN_DELAY = 120  # секунд после старта: проверка не мешает запуску
_INTERVAL = 24 * 3600
# Самая долгая игра — крестики-нолики: до 50 ходов по 10 минут. Сутки без движения — потеря
_POT_IDLE = timedelta(days=1)
_EMPTY_POT_KEEP = timedelta(days=7)


@dataclass(slots=True)
class AuditResult:
    mismatches: list[LedgerMismatch]
    unknown_last_day: int
    stale_pots: list[GamePot]


async def run_audit(container) -> AuditResult:
    now = datetime.now(timezone.utc)
    async with container() as scope:
        repo = await scope.get(ILedgerRepository)
        pots = await scope.get(IGamePotRepository)
        mismatches = await repo.mismatches()
        unknown = await repo.count_unknown_since(now - timedelta(days=1))
        stale_pots = await pots.stale(now - _POT_IDLE)
        await pots.purge_empty(now - _EMPTY_POT_KEEP)

    for m in mismatches:
        logger.error(
            "ledger: баланс не сходится с журналом: chat=%d user=%d баланс=%d журнал=%d",
            m.chat_id,
            m.user_id,
            m.balance,
            m.ledger_total,
        )
    if unknown:
        logger.warning("ledger: %d изменений баланса за сутки без типа операции — правка мимо кода", unknown)
    for pot in stale_pots:
        logger.warning(
            "ledger: в банке игры %s (чат %d) %d баллов без движения с %s — игра потеряна без расчёта, "
            "ставки по журналу: SELECT user_id, delta FROM ledger WHERE ref = '%s'",
            pot.ref,
            pot.chat_id,
            pot.amount,
            pot.updated_at.isoformat(timespec="minutes"),
            pot.ref,
        )
    return AuditResult(mismatches=mismatches, unknown_last_day=unknown, stale_pots=stale_pots)


async def ledger_audit_loop(container) -> None:
    await asyncio.sleep(_FIRST_RUN_DELAY)
    while True:
        try:
            await run_audit(container)
        except Exception:
            logger.exception("ledger: сверка не удалась")
        await asyncio.sleep(_INTERVAL)
