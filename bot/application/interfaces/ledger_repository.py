from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


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
    async def count_unknown_since(self, since: datetime) -> int:
        """Сколько изменений баланса с ``since`` прошло без типа операции (мимо кода)."""
        ...
