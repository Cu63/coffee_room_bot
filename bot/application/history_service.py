from datetime import datetime, timedelta

from bot.application.interfaces.ledger_repository import ILedgerRepository
from bot.domain.ledger import LedgerEntry
from bot.domain.tz import TZ_MSK


class HistoryService:
    """История операций с баллами по журналу ledger: реакции, переводы, покупки, игры, бонусы."""

    def __init__(self, ledger_repo: ILedgerRepository, retention_days: int) -> None:
        self._ledger = ledger_repo
        self._retention_days = retention_days

    def _since(self) -> datetime:
        return datetime.now(TZ_MSK) - timedelta(days=self._retention_days)

    async def get_history(self, chat_id: int) -> list[LedgerEntry]:
        return await self._ledger.history(chat_id, self._since())

    async def get_user_history(self, chat_id: int, user_id: int) -> list[LedgerEntry]:
        return await self._ledger.history(chat_id, self._since(), user_id=user_id)
