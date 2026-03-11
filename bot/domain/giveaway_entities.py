from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class GiveawayStatus(str, Enum):
    ACTIVE = "active"
    FINISHED = "finished"


@dataclass(slots=True)
class Giveaway:
    chat_id: int
    created_by: int
    prizes: list[int]
    status: GiveawayStatus = GiveawayStatus.ACTIVE
    message_id: int | None = None
    ends_at: datetime | None = None
    created_at: datetime | None = None
    id: int | None = None


@dataclass(slots=True)
class GiveawayWinner:
    giveaway_id: int
    user_id: int
    prize: int
    position: int  # 1-based