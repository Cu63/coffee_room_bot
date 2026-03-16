from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import yaml
from adaptix import Retort
from dynaconf import Dynaconf

# Папка с конфигами относительно корня проекта
_CONFIGS_DIR = Path("configs")


@dataclass(slots=True, kw_only=True)
class Settings:
    bot_token: str = ""
    database_url: str = "postgresql://scorebot:scorebot@db:5432/scorebot"
    aitunnel_api_key: str = ""
    openserp_url: str = "http://openserp:7000"
    redis_url: str = "redis://redis:6379/0"
    log_chat_id: int = 0  # Telegram chat ID для отправки логов (0 = отключено)
    log_level: str = "ERROR"  # уровень логов для Telegram: ERROR, WARNING, INFO


@dataclass(slots=True, kw_only=True)
class ScoreConfig:
    singular: str = "балл"
    plural_few: str = "балла"
    plural_many: str = "баллов"
    icon: str = "⭐"


@dataclass(slots=True, kw_only=True)
class LimitsConfig:
    daily_negative_given: int = 10
    daily_positive_per_target: int = 20
    daily_score_received: int = 50
    max_message_age_hours: int = 36


@dataclass(slots=True, kw_only=True)
class SlotsConfig:
    min_bet: int = 1
    max_bet: int = 25
    cooldown_minutes: int = 60


@dataclass(slots=True, kw_only=True)
class HistoryConfig:
    retention_days: int = 7
    page_size: int = 30


@dataclass(slots=True, kw_only=True)
class AdminConfig:
    prefix: str = "admin"
    users: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class AutoReactConfig:
    enabled: bool = False
    probability: float = 0.05
    positive_only: bool = True


@dataclass(slots=True, kw_only=True)
class MuteConfig:
    cost_per_minute: int = 3
    min_minutes: int = 1
    max_minutes: int = 15
    daily_limit: int = 3
    target_cooldown_hours: int = 2
    selfmute_min_minutes: int = 1
    selfmute_max_minutes: int = 1440
    protection_cost: int = 200
    protection_duration_hours: int = 24


@dataclass(slots=True, kw_only=True)
class TagConfig:
    cost_self: int = 50
    cost_member: int = 100
    cost_admin: int = 200
    cost_owner: int = 500
    max_length: int = 32


@dataclass(slots=True, kw_only=True)
class BlackjackConfig:
    min_bet: int = 1
    max_bet: int = 50
    max_games_per_window: int = 5
    window_hours: int = 1
    game_timeout_seconds: int = 60


@dataclass(slots=True, kw_only=True)
class DiceConfig:
    min_bet: int = 1
    max_bet: int = 1000
    min_wait_seconds: int = 10
    max_wait_seconds: int = 900


@dataclass(slots=True, kw_only=True)
class SystemConfig:
    """Системные интервалы и технические параметры."""

    cleanup_interval_hours: int = 6
    unmute_check_interval_seconds: int = 60
    auto_delete_seconds: int = 120
    history_page_size: int = 30


@dataclass(slots=True, kw_only=True)
class LlmConfig:
    model: str = "gemini-2.5-flash-lite"
    base_url: str = "https://api.aitunnel.ru/v1"
    max_output_tokens: int = 1024
    daily_limit_per_user: int = 10
    search_max_results: int = 5
    system_prompt: str = (
        "Отвечай кратко и по делу на русском языке. "
        "Форматируй в Telegram HTML. Допустимые теги: "
        '<b>, <i>, <u>, <s>, <code>, <pre>, <blockquote>, <a href="URL">текст</a>. '
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
        '«<a href="URL">Dyson V15</a> имеет мощность 230 Вт»\n'
        "- Текст ссылки — КОРОТКИЙ: название модели или домен.\n"
        "- Используй ТОЛЬКО URL из предоставленных источников.\n"
        "- В конце добавь блок:\n<b>Источники:</b>\n"
        '— <a href="URL">короткое название</a>\n\n'
        "ЛИМИТ: ответ НЕ БОЛЕЕ 3500 символов.\n\n"
        "ФОРМАТ — строго Telegram HTML:\n"
        '- <b>жирный</b>, <i>курсив</i>, <a href="URL">текст</a>\n'
        "- Списки: «— » или «1. »\n"
        "- ЗАПРЕЩЕНО: **, *, <sup>, <sub>, <span>, <div>, [текст](url)\n"
        "- ВСЕ теги ОБЯЗАТЕЛЬНО закрывай."
    )


@dataclass(slots=True, kw_only=True)
class RenewConfig:
    cost: int = 100
    daily_limit: int = 2


@dataclass(slots=True, kw_only=True)
class BugConfig:
    """Конфиг для команды /bug — кому отправлять баг-репорты."""

    recipients: list[int] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class LoggingConfig:
    """Настройки логирования через structlog."""

    level: str = "INFO"
    human_readable_logs: bool = False  # True = цветной консольный вывод (dev), False = JSON (prod)

@dataclass(slots=True, kw_only=True)
class WordgameConfig:
    min_bet: int = 0
    max_bet: int = 1000
    min_duration_seconds: int = 180    # 3 минуты
    max_duration_seconds: int = 3600   # 1 час
    attempt_cost: int = 1              # баллов за неудачную попытку
    min_word_length: int = 2
    max_word_length: int = 32

@dataclass(slots=True, kw_only=True)
class AppConfig:
    score: ScoreConfig
    reactions: dict[str, int] = field(default_factory=dict)
    self_reaction_allowed: bool = False
    limits: LimitsConfig
    history: HistoryConfig
    admin: AdminConfig
    mute: MuteConfig
    auto_react: AutoReactConfig
    tag: TagConfig
    blackjack: BlackjackConfig
    slots: SlotsConfig
    dice: DiceConfig
    llm: LlmConfig
    system: SystemConfig
    renew: RenewConfig
    bug: BugConfig
    logging: LoggingConfig
    wordgame: WordgameConfig


T = TypeVar("T")


def load_settings[T](env_file: str | None = None, config: type[T] | None = None) -> T:
    dyna = Dynaconf(
        dotenv_path=env_file or ".env",
        load_dotenv=True,
        lowercase_read=True,
        envvar_prefix="CONFIG",
    )
    return Retort().load(dyna, config or Settings)


def load_config[T](filename: str, config: type[T]) -> T:
    dyna = Dynaconf(
        settings_files=[_CONFIGS_DIR / filename],
        merge_enabled=True,
    )
    return Retort().load(dyna, config)


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


if __name__ == "__main__":
    from rich import print as rprint

    app_config = load_config("config.yaml", AppConfig)
    settings = load_settings()

    rprint(app_config)
    rprint(settings)
