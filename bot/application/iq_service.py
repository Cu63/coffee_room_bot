"""IqService — управление IQ пользователей (по чатам).

IQ хранится по паре (user_id, chat_id). Стартовое значение у всех — из
config.iq.default (по умолчанию 89). IQ может быть отрицательным.
"""

from __future__ import annotations

from bot.application.interfaces.iq_repository import IIqRepository, UserIq
from bot.infrastructure.config_loader import IqConfig


class IqService:
    def __init__(self, iq_repo: IIqRepository, config: IqConfig) -> None:
        self._repo = iq_repo
        self._cfg = config

    async def get_iq(self, user_id: int, chat_id: int) -> int:
        """Текущий IQ пользователя (config.iq.default, если записи ещё нет)."""
        value = await self._repo.get_iq(user_id, chat_id)
        return value if value is not None else self._cfg.default

    async def add_iq(self, user_id: int, chat_id: int, delta: int) -> int:
        """Изменяет IQ на delta (может быть отрицательным). Возвращает новый IQ."""
        return await self._repo.add_iq(user_id, chat_id, delta, self._cfg.default)

    async def get_top(self, chat_id: int, limit: int) -> list[UserIq]:
        """Топ пользователей чата по IQ."""
        return await self._repo.top(chat_id, limit)
