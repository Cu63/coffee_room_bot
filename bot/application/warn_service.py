"""WarnService — предупреждения модераторов: набрал лимит за срок — пора в мут."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.application.interfaces.warn_repository import IWarnRepository
from bot.domain.entities import WarnEntry
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import WarnConfig


@dataclass(slots=True, frozen=True)
class WarnResult:
    count: int  # действующих предупреждений, с этим
    limit: int

    @property
    def reached(self) -> bool:
        return self.count >= self.limit


class WarnService:
    def __init__(self, repo: IWarnRepository, config: WarnConfig) -> None:
        self._repo = repo
        self._cfg = config

    def _since(self) -> datetime:
        return datetime.now(TZ_MSK) - timedelta(days=self._cfg.period_days)

    async def warn(self, chat_id: int, user_id: int, issued_by: int, reason: str) -> WarnResult:
        """Выдать предупреждение. Блокировка до конца транзакции: два модератора, предупредившие
        одновременно, не насчитают лимит дважды и не замутят дважды."""
        await self._repo.lock(chat_id, user_id)
        await self._repo.add(chat_id, user_id, issued_by, reason)
        count = len(await self._repo.active(chat_id, user_id, self._since()))
        return WarnResult(count=count, limit=self._cfg.limit)

    async def active(self, chat_id: int, user_id: int) -> list[WarnEntry]:
        """Действующие предупреждения — сначала новые."""
        return await self._repo.active(chat_id, user_id, self._since())

    async def unwarn(self, chat_id: int, user_id: int) -> int | None:
        """Снять последнее предупреждение. Возвращает, сколько осталось; None — снимать нечего."""
        await self._repo.lock(chat_id, user_id)
        if not await self._repo.delete_latest(chat_id, user_id, self._since()):
            return None
        return len(await self._repo.active(chat_id, user_id, self._since()))

    async def clear(self, chat_id: int, user_id: int) -> None:
        """Предупреждения «сгорают», когда за них выдан мут."""
        await self._repo.clear(chat_id, user_id)
