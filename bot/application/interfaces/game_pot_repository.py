from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PotTake(StrEnum):
    TAKEN = "taken"
    SHORT = "short"  # банк есть, но в нём меньше: игру рассчитывают второй раз
    MISSING = "missing"  # банка нет вовсе


@dataclass(slots=True, frozen=True)
class GamePot:
    chat_id: int
    ref: str
    amount: int
    updated_at: datetime


class IGamePotRepository(ABC):
    @abstractmethod
    async def put(self, chat_id: int, ref: str, amount: int) -> None:
        """Положить ставку в банк игры (банк создаётся при первой ставке)."""
        ...

    @abstractmethod
    async def take(self, chat_id: int, ref: str, amount: int) -> PotTake:
        """Атомарно взять из банка, только если в нём хватает денег."""
        ...

    @abstractmethod
    async def stale(self, idle_since: datetime) -> list[GamePot]:
        """Банки с деньгами, которые не трогали с idle_since: игры, потерянные без расчёта."""
        ...

    @abstractmethod
    async def purge_empty(self, idle_since: datetime) -> int:
        """Удалить опустевшие банки, которые не трогали с idle_since."""
        ...
