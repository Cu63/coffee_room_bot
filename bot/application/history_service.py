from datetime import datetime, timedelta
from bot.domain.tz import TZ_MSK

from bot.domain.entities import ScoreEvent
from bot.application.interfaces.event_repository import IEventRepository


class HistoryService:
    def __init__(self, event_repo: IEventRepository, retention_days: int) -> None:
        self._event_repo = event_repo
        self._retention_days = retention_days

    async def get_history(self, chat_id: int) -> list[ScoreEvent]:
        since = datetime.now(TZ_MSK) - timedelta(days=self._retention_days)
        return await self._event_repo.get_history(chat_id, since)

    async def get_user_history(self, chat_id: int, user_id: int) -> list[ScoreEvent]:
        since = datetime.now(TZ_MSK) - timedelta(days=self._retention_days)
        return await self._event_repo.get_history_by_user(chat_id, user_id, since)