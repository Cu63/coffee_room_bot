"""Фоновая задача: сверка журнала операций с балансами.

Журнал ведёт триггер на scores (V017__ledger.sql), поэтому сумма журнала по паре
(чат, пользователь) обязана совпадать с балансом. Расхождение значит, что журнал
правили вручную или триггер отключали — это ERROR. Изменения без типа операции
(kind = 'unknown') значат правку баланса мимо кода — WARNING.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bot.application.interfaces.ledger_repository import ILedgerRepository, LedgerMismatch

logger = logging.getLogger(__name__)

_FIRST_RUN_DELAY = 120  # секунд после старта: проверка не мешает запуску
_INTERVAL = 24 * 3600


@dataclass(slots=True)
class AuditResult:
    mismatches: list[LedgerMismatch]
    unknown_last_day: int


async def run_audit(container) -> AuditResult:
    async with container() as scope:
        repo = await scope.get(ILedgerRepository)
        mismatches = await repo.mismatches()
        unknown = await repo.count_unknown_since(datetime.now(timezone.utc) - timedelta(days=1))

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
    return AuditResult(mismatches=mismatches, unknown_last_day=unknown)


async def ledger_audit_loop(container) -> None:
    await asyncio.sleep(_FIRST_RUN_DELAY)
    while True:
        try:
            await run_audit(container)
        except Exception:
            logger.exception("ledger: сверка не удалась")
        await asyncio.sleep(_INTERVAL)
