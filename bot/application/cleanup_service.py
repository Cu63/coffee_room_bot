from datetime import datetime, timedelta, timezone
import logging

from bot.application.interfaces.event_repository import IEventRepository

logger = logging.getLogger(__name__)


class CleanupService:
    def __init__(self, event_repo: IEventRepository, retention_days: int) -> None:
        self._event_repo = event_repo
        self._retention_days = retention_days

    async def delete_expired_events(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        deleted = await self._event_repo.delete_before(cutoff)
        if deleted:
            logger.info("Deleted %d expired score events", deleted)
        return deleted
