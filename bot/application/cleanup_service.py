import logging
from datetime import datetime, timedelta

from bot.application.interfaces.event_repository import IEventRepository
from bot.application.interfaces.message_repository import IMessageRepository
from bot.domain.tz import TZ_MSK

logger = logging.getLogger(__name__)


class CleanupService:
    def __init__(
        self,
        event_repo: IEventRepository,
        message_repo: IMessageRepository,
        retention_days: int,
        messages_retention_days: int,
    ) -> None:
        self._event_repo = event_repo
        self._message_repo = message_repo
        self._retention_days = retention_days
        self._messages_retention_days = messages_retention_days

    async def delete_expired_events(self) -> int:
        cutoff = datetime.now(TZ_MSK) - timedelta(days=self._retention_days)
        deleted = await self._event_repo.delete_before(cutoff)
        if deleted:
            logger.info("Deleted %d expired score events", deleted)
        return deleted

    async def delete_expired_messages(self) -> int:
        """Тексты сообщений нужны саммари, /analyze, /ff и проверке реакций — на дни, не навсегда."""
        cutoff = datetime.now(TZ_MSK) - timedelta(days=self._messages_retention_days)
        deleted = await self._message_repo.delete_before(cutoff)
        if deleted:
            logger.info("Deleted %d messages older than %d days", deleted, self._messages_retention_days)
        return deleted
