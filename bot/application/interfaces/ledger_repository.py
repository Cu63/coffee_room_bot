from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from bot.domain.ledger import LedgerEntry


@dataclass(slots=True, frozen=True)
class LedgerMismatch:
    chat_id: int
    user_id: int
    balance: int  # scores.value
    ledger_total: int  # сумма ledger.delta


class ILedgerRepository(ABC):
    """Журнал операций с баллами (ledger) — для сверки. Пишет в него триггер на scores."""

    @abstractmethod
    async def mismatches(self, limit: int = 100) -> list[LedgerMismatch]:
        """Пары (чат, пользователь), у которых баланс не равен сумме журнала."""
        ...

    @abstractmethod
    async def history(
        self, chat_id: int, since: datetime, user_id: int | None = None, limit: int = 500
    ) -> list[LedgerEntry]:
        """Операции участников чата (без кассы бота и стартовых остатков), новые первыми.

        С ``user_id`` — только операции, где он одна из сторон.
        """
        ...

    @abstractmethod
    async def count_unknown_since(self, since: datetime) -> int:
        """Сколько изменений баланса с ``since`` прошло без типа операции (мимо кода)."""
        ...
