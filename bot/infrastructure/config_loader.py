from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "postgresql://scorebot:scorebot@db:5432/scorebot"
    aitunnel_api_key: str = ""
    openserp_url: str = "http://openserp:7000"

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
class LlmConfig:
    model: str = "gemini-2.5-flash-lite"
    base_url: str = "https://api.aitunnel.ru/v1"
    max_output_tokens: int = 1024
    daily_limit_per_user: int = 10
    search_max_results: int = 5
    system_prompt: str = (
        "Отвечай кратко и по делу на русском языке. "
        "Форматируй в Telegram HTML. Допустимые теги: "
        "<b>, <i>, <u>, <s>, <code>, <pre>, <blockquote>, <a href=\"URL\">текст</a>. "
        "ВСЕ теги ОБЯЗАТЕЛЬНО закрывай."
    )
    search_system_prompt: str = (
        "Ты — поисковый ассистент. Тебе даны результаты поиска "
        "с извлечённым контентом страниц.\n\n"
        "Дай развёрнутый ответ на русском, используя ТОЛЬКО факты "
        "из предоставленных источников. Если данных недостаточно, "
        "так и напиши.\n\n"
        "ССЫЛКИ — ОБЯЗАТЕЛЬНО:\n"
        "- КАЖДЫЙ упомянутый товар/модель/факт ДОЛЖЕН иметь ссылку.\n"
        "- НЕ упоминай товар без ссылки. Если нет URL — не упоминай.\n"
        "- Вставляй ссылки ИНЛАЙН: "
        "«<a href=\"URL\">Dyson V15</a> имеет мощность 230 Вт»\n"
        "- Текст ссылки — КОРОТКИЙ: название модели или домен.\n"
        "- Используй ТОЛЬКО URL из предоставленных источников.\n"
        "- В конце добавь блок:\n<b>Источники:</b>\n"
        "— <a href=\"URL\">короткое название</a>\n\n"
        "ЛИМИТ: ответ НЕ БОЛЕЕ 3500 символов.\n\n"
        "ФОРМАТ — строго Telegram HTML:\n"
        "- <b>жирный</b>, <i>курсив</i>, <a href=\"URL\">текст</a>\n"
        "- Списки: «— » или «1. »\n"
        "- ЗАПРЕЩЕНО: **, *, <sup>, <sub>, <span>, <div>, [текст](url)\n"
        "- ВСЕ теги ОБЯЗАТЕЛЬНО закрывай."
    )


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
    llm: LlmConfig = field(default_factory=LlmConfig)


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
    llm_raw = raw.get("llm", {})

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
        llm=LlmConfig(**llm_raw),
    )


def load_messages(path: str | Path = "messages.yaml") -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)