from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings

# Папка с конфигами относительно корня проекта
_CONFIGS_DIR = Path("configs")


class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "postgresql://scorebot:scorebot@db:5432/scorebot"
    aitunnel_api_key: str = ""
    openserp_url: str = "http://openserp:7000"
    redis_url: str = "redis://redis:6379/0"
    log_chat_id: int = 0  # Telegram chat ID для отправки логов (0 = отключено)
    log_level: str = "ERROR"  # уровень логов для Telegram: ERROR, WARNING, INFO

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
    page_size: int = 30


@dataclass
class AdminConfig:
    prefix: str = "admin"
    users: list[str] = field(default_factory=list)

    def cmd(self, action: str) -> str:
        return f"{self.prefix}_{action}"


@dataclass
class AutoReactConfig:
    enabled: bool = False
    probability: float = 0.05
    positive_only: bool = True


@dataclass
class MuteConfig:
    cost_per_minute: int = 20
    min_minutes: int = 1
    max_minutes: int = 120
    selfmute_min_minutes: int = 1
    selfmute_max_minutes: int = 1440
    protection_cost: int = 200
    protection_duration_hours: int = 24


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
    max_games_per_window: int = 5
    window_hours: int = 1


@dataclass
class SystemConfig:
    """Системные интервалы и технические параметры."""
    cleanup_interval_hours: int = 6
    unmute_check_interval_seconds: int = 60
    auto_delete_seconds: int = 120
    history_page_size: int = 30


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
    auto_react: AutoReactConfig = field(default_factory=AutoReactConfig)
    tag: TagConfig = field(default_factory=TagConfig)
    blackjack: BlackjackConfig = field(default_factory=BlackjackConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    system: SystemConfig = field(default_factory=SystemConfig)


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        path = _CONFIGS_DIR / "config.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    users = [u.lstrip("@").lower() for u in raw.get("admin", {}).get("users", [])]
    admin_raw = raw.get("admin", {})
    history_raw = raw.get("history", {})
    blackjack_raw = raw.get("blackjack", {})
    system_raw = raw.get("system", {})

    return AppConfig(
        score=ScoreConfig(**raw.get("score", {})),
        reactions=raw.get("reactions", {}),
        self_reaction_allowed=raw.get("self_reaction_allowed", False),
        limits=LimitsConfig(**raw.get("limits", {})),
        history=HistoryConfig(**history_raw),
        admin=AdminConfig(prefix=admin_raw.get("prefix", "admin"), users=users),
        mute=MuteConfig(**raw.get("mute", {})),
        auto_react=AutoReactConfig(**raw.get("auto_react", {})),
        tag=TagConfig(**raw.get("tag", {})),
        blackjack=BlackjackConfig(**blackjack_raw),
        llm=LlmConfig(**raw.get("llm", {})),
        system=SystemConfig(**system_raw),
    )


def load_messages(path: str | Path | None = None) -> dict[str, str]:
    if path is None:
        path = _CONFIGS_DIR / "messages.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_help_config(path: str | Path | None = None) -> dict:
    """Загружает configs/help.yaml — тексты и структуру /help меню."""
    if path is None:
        path = _CONFIGS_DIR / "help.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
