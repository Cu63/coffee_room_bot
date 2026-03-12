from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DiceGameStatus(str, Enum):
    PENDING = "pending"
    FINISHED = "finished"


@dataclass
class DiceGame:
    chat_id: int
    bet: int
    ends_at: datetime
    created_by: int
    id: int | None = None
    message_id: int | None = None
    status: DiceGameStatus = DiceGameStatus.PENDING
    created_at: datetime | None = None
