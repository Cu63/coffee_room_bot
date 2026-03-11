from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "postgresql://scorebot:scorebot@db:5432/scorebot"

    model_config = {"env_file": ".env", "extra": "ignore"}


@dataclass
class ScoreConfig:
    singular: str = "балл"
    plural_few: str = "балла"
    plural_many: str = "баллов"
    icon: str = "⭐"


@dataclass
class LimitsConfig:
    daily_reactions_given: int = 10
    daily_score_received: int = 20
    max_message_age_hours: int = 48


@dataclass
class HistoryConfig:
    retention_days: int = 7


@dataclass
class AdminConfig:
    prefix: str = "admin"
    users: list[str] = field(default_factory=list)

    def cmd(self, action: str) -> str:
        """Возвращает имя команды: prefix_action (например 'coffee_add')."""
        return f"{self.prefix}_{action}"


@dataclass
class MuteConfig:
    cost_per_minute: int = 20
    min_minutes: int = 1
    max_minutes: int = 120


@dataclass
class TagConfig:
    cost_self: int = 50
    cost_member: int = 100
    cost_admin: int = 200
    cost_owner: int = 500
    max_length: int = 32


@dataclass
class BlackjackConfig:
    min_bet: int = 1
    max_bet: int = 500


@dataclass
class AppConfig:
    score: ScoreConfig = field(default_factory=ScoreConfig)
    reactions: dict[str, int] = field(default_factory=dict)
    self_reaction_allowed: bool = False
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    mute: MuteConfig = field(default_factory=MuteConfig)
    tag: TagConfig = field(default_factory=TagConfig)
    blackjack: BlackjackConfig = field(default_factory=BlackjackConfig)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    score_raw = raw.get("score", {})
    limits_raw = raw.get("limits", {})
    history_raw = raw.get("history", {})
    admin_raw = raw.get("admin", {})
    mute_raw = raw.get("mute", {})
    tag_raw = raw.get("tag", {})
    blackjack_raw = raw.get("blackjack", {})

    # Нормализуем username: убираем @ если есть, приводим к lower
    users = [u.lstrip("@").lower() for u in admin_raw.get("users", [])]

    return AppConfig(
        score=ScoreConfig(**score_raw),
        reactions=raw.get("reactions", {}),
        self_reaction_allowed=raw.get("self_reaction_allowed", False),
        limits=LimitsConfig(**limits_raw),
        history=HistoryConfig(**history_raw),
        admin=AdminConfig(
            prefix=admin_raw.get("prefix", "admin"),
            users=users,
        ),
        mute=MuteConfig(**mute_raw),
        tag=TagConfig(**tag_raw),
        blackjack=BlackjackConfig(**blackjack_raw),
    )


def load_messages(path: str | Path = "messages.yaml") -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)