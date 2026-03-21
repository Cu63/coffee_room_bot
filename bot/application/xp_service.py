"""XpService — начисление опыта и вычисление уровня."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.application.interfaces.xp_repository import IXpRepository
from bot.infrastructure.config_loader import XpConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class XpResult:
    """Результат начисления XP."""
    new_xp: int
    old_level: int
    new_level: int

    @property
    def leveled_up(self) -> bool:
        return self.new_level > self.old_level


class XpService:
    def __init__(self, xp_repo: IXpRepository, config: XpConfig) -> None:
        self._repo = xp_repo
        self._cfg = config

    def compute_level(self, xp: int) -> int:
        """Вычисляет уровень по количеству XP (линейная шкала)."""
        if self._cfg.levels.xp_per_level <= 0:
            return 1
        level = xp // self._cfg.levels.xp_per_level + 1
        return min(level, self._cfg.levels.max_level)

    def xp_for_next_level(self, current_xp: int) -> int | None:
        """XP до следующего уровня. None если уже максимальный уровень."""
        level = self.compute_level(current_xp)
        if level >= self._cfg.levels.max_level:
            return None
        return self._cfg.levels.xp_per_level * level - current_xp

    async def add_xp(self, user_id: int, chat_id: int, amount: int) -> XpResult:
        """Добавляет XP и возвращает результат с информацией об уровне."""
        old_xp = await self._repo.get_xp(user_id, chat_id)
        old_level = self.compute_level(old_xp)
        new_xp = await self._repo.add_xp(user_id, chat_id, amount)
        new_level = self.compute_level(new_xp)
        if new_level > old_level:
            logger.info(
                "xp: user %d in chat %d leveled up %d → %d (xp: %d → %d)",
                user_id, chat_id, old_level, new_level, old_xp, new_xp,
            )
        return XpResult(new_xp=new_xp, old_level=old_level, new_level=new_level)

    async def get_xp(self, user_id: int, chat_id: int) -> int:
        return await self._repo.get_xp(user_id, chat_id)

    async def get_top(self, chat_id: int, limit: int = 10):
        """Топ пользователей по XP в чате."""
        return await self._repo.top(chat_id, limit)
