from datetime import datetime, timedelta, timezone

from bot.domain.entities import ScoreEvent
from bot.application.interfaces.event_repository import IEventRepository


class HistoryService:
    def __init__(self, event_repo: IEventRepository, retention_days: int) -> None:
        self._event_repo = event_repo
        self._retention_days = retention_days

    async def get_history(self, chat_id: int) -> list[ScoreEvent]:
        since = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        return await self._event_repo.get_history(chat_id, since)
