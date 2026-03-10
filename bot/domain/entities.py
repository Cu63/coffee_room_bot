from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


@dataclass(slots=True)
class User:
    id: int
    username: str | None
    full_name: str


@dataclass(slots=True)
class Score:
    user_id: int
    chat_id: int
    value: int


@dataclass(slots=True)
class Reaction:
    emoji: str
    weight: int


class Direction(str, Enum):
    ADD = "ADD"
    REMOVE = "REMOVE"


@dataclass(slots=True)
class ScoreEvent:
    chat_id: int
    actor_id: int
    target_id: int
    message_id: int
    emoji: str
    delta: int
    direction: Direction
    created_at: datetime | None = None
    id: int | None = None


@dataclass(slots=True)
class DailyLimits:
    user_id: int
    chat_id: int
    date: datetime
    reactions_given: int = 0
    score_received: int = 0


class IgnoreReason(str, Enum):
    UNKNOWN_REACTION = "unknown_reaction"
    ZERO_WEIGHT = "zero_weight"
    SELF_REACTION = "self_reaction"
    MESSAGE_TOO_OLD = "message_too_old"
    DUPLICATE = "duplicate"
    DAILY_REACTIONS_LIMIT = "daily_reactions_limit"
    DAILY_SCORE_LIMIT = "daily_score_limit"
    UNKNOWN_MESSAGE = "unknown_message"
    EVENT_NOT_FOUND = "event_not_found"
    NEGATIVE_SCORE_ACTOR = "negative_score_actor"


@dataclass(slots=True)
class ApplyResult:
    applied: bool
    delta: int = 0
    new_value: int = 0
    reason: IgnoreReason | None = None


@dataclass(slots=True)
class MuteEntry:
    user_id: int
    chat_id: int
    muted_by: int
    until_at: datetime
    soft_mute: bool = False
    was_admin: bool = False
    admin_permissions: dict | None = None