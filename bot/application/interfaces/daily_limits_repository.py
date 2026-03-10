from abc import ABC, abstractmethod
from datetime import date

from bot.domain.entities import DailyLimits


class IDailyLimitsRepository(ABC):
    @abstractmethod
    async def get(self, user_id: int, chat_id: int, day: date) -> DailyLimits: ...

    @abstractmethod
    async def increment_given(self, user_id: int, chat_id: int, day: date, delta: int) -> None: ...

    @abstractmethod
    async def increment_received(self, user_id: int, chat_id: int, day: date, delta: int) -> None: ...
