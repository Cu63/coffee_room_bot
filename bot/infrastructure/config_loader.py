from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Папка с конфигами относительно корня проекта
_CONFIGS_DIR = Path("configs")


class DatabaseConfig(BaseSettings):
    """PostgreSQL DSN. Читается из DATABASE_URL (или DSN) в окружении / .env.

    Поддерживает оба формата драйвера:
      postgresql://...          — asyncpg (предпочтительно)
      postgresql+asyncpg://...  — SQLAlchemy-style (суффикс обрезается в get_pool)
    """

    dsn: str = Field(
        default="postgresql://scorebot:scorebot@db:5432/scorebot",
        validation_alias=AliasChoices("database_url", "dsn"),
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


class BotSettings(BaseSettings):
    """Настройки бота из переменных окружения (.env).

    Всё, кроме базы данных — та живёт в :class:`DatabaseConfig`.
    """

    bot_token: str = ""
    aitunnel_api_key: str = ""
    openserp_url: str = "http://openserp:7000"
    redis_url: str = "redis://redis:6379/0"
    log_chat_id: int = 0   # Telegram chat ID для отправки логов (0 = отключено)
    log_level: str = "ERROR"  # уровень логов для Telegram: ERROR, WARNING, INFO

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class _BaseConfig(BaseModel):
    """Базовая модель с общими настройками для всех конфигов."""

    model_config = ConfigDict(extra="ignore")


class ScoreConfig(_BaseConfig):
    singular: str = "балл"
    plural_few: str = "балла"
    plural_many: str = "баллов"
    icon: str = "⭐"


class LimitsConfig(_BaseConfig):
    daily_negative_given: int = 10
    daily_positive_per_target: int = 20
    daily_score_received: int = 50
    max_message_age_hours: int = 36


class SlotsConfig(_BaseConfig):
    min_bet: int = 1
    max_bet: int = 25
    cooldown_minutes: int = 60


class HistoryConfig(_BaseConfig):
    retention_days: int = 7
    page_size: int = 30


class AdminConfig(_BaseConfig):
    prefix: str = "admin"
    users: list[str] = []

    @field_validator("users", mode="before")
    @classmethod
    def normalize_users(cls, v: list[str] | None) -> list[str]:
        """Приводит имена пользователей к нижнему регистру и убирает @."""
        return [u.lstrip("@").lower() for u in (v or [])]


class AutoReactConfig(_BaseConfig):
    enabled: bool = False
    probability: float = 0.05
    positive_only: bool = True


class MuteConfig(_BaseConfig):
    cost_per_minute: int = 3
    min_minutes: int = 1
    max_minutes: int = 15
    daily_limit: int = 3
    target_cooldown_hours: int = 2
    selfmute_min_minutes: int = 1
    selfmute_max_minutes: int = 1440
    protection_cost: int = 200
    protection_duration_hours: int = 24
    unmute_multiplier: float = 1.5


class TagConfig(_BaseConfig):
    cost_self: int = 50
    cost_member: int = 100
    cost_admin: int = 200
    cost_owner: int = 500
    max_length: int = 32


class BlackjackConfig(_BaseConfig):
    min_bet: int = 1
    max_bet: int = 50
    max_games_per_window: int = 5
    window_hours: int = 1
    game_timeout_seconds: int = 60


class DiceConfig(_BaseConfig):
    min_bet: int = 1
    max_bet: int = 1000
    min_wait_seconds: int = 10
    max_wait_seconds: int = 900


class BurstConfig(_BaseConfig):
    enabled: bool = False
    messages_required: int = 8
    window_minutes: int = 20
    min_length: int = 15
    reward: int = 15
    cooldown_hours: int = 4


class SparkConfig(_BaseConfig):
    enabled: bool = False
    unique_responders: int = 4
    window_minutes: int = 10
    reward: int = 10
    cooldown_hours: int = 6


class ReplyChainConfig(_BaseConfig):
    enabled: bool = False
    replies_required: int = 6
    window_minutes: int = 15
    reward: int = 8
    cooldown_hours: int = 4


class SystemConfig(_BaseConfig):
    """Системные интервалы и технические параметры."""

    cleanup_interval_hours: int = 6
    unmute_check_interval_seconds: int = 60
    auto_delete_seconds: int = 120
    history_page_size: int = 30


class LlmConfig(_BaseConfig):
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


class RenewConfig(_BaseConfig):
    cost: int = 100
    daily_limit: int = 2


class WordgameConfig(_BaseConfig):
    min_bet: int = 0
    max_bet: int = 1000
    min_duration_seconds: int = 180    # 3 минуты
    max_duration_seconds: int = 3600   # 1 час
    attempt_cost: int = 1              # баллов за неудачную попытку
    min_word_length: int = 2
    max_word_length: int = 32
    max_games_per_window: int = 3      # макс. игр за окно
    game_window_hours: int = 4         # окно лимита в часах


class RwordgameConfig(_BaseConfig):
    """Настройки /rword — угадайка с рандомным словом из словаря."""
    max_bet: int = 50                  # максимальная ставка (лимит N)
    min_word_length: int = 4           # минимальная длина загадываемого слова
    max_word_length: int = 9           # максимальная длина загадываемого слова
    words_file: str = "configs/words_ru.txt"

class BugConfig(_BaseConfig):
    """Конфиг для команды /bug — кому отправлять баг-репорты."""
    recipients: list[int] = []


class LoggingConfig(_BaseConfig):
    """Настройки логирования через structlog."""
    level: int = logging.INFO
    human_readable_logs: bool = False  # True = цветной консольный вывод (dev), False = JSON (prod)

    @field_validator("level", mode="before")
    @classmethod
    def parse_level(cls, v) -> int:
        """Принимает строку ('INFO', 'DEBUG') или число."""
        if isinstance(v, str):
            numeric = getattr(logging, v.upper(), None)
            if numeric is None:
                raise ValueError(f"Неизвестный уровень логирования: {v!r}")
            return numeric
        return v


class AppConfig(_BaseConfig):
    score: ScoreConfig = ScoreConfig()
    reactions: dict[str, int] = {}
    self_reaction_allowed: bool = False
    limits: LimitsConfig = LimitsConfig()
    history: HistoryConfig = HistoryConfig()
    admin: AdminConfig = AdminConfig()
    mute: MuteConfig = MuteConfig()
    auto_react: AutoReactConfig = AutoReactConfig()
    tag: TagConfig = TagConfig()
    blackjack: BlackjackConfig = BlackjackConfig()
    slots: SlotsConfig = SlotsConfig()
    dice: DiceConfig = DiceConfig()
    llm: LlmConfig = LlmConfig()
    burst: BurstConfig = BurstConfig()
    spark: SparkConfig = SparkConfig()
    reply_chain: ReplyChainConfig = ReplyChainConfig()
    system: SystemConfig = SystemConfig()
    renew: RenewConfig = RenewConfig()
    wordgame: WordgameConfig = WordgameConfig()
    rwordgame: RwordgameConfig = RwordgameConfig()
    bug: BugConfig = BugConfig()
    logging: LoggingConfig = LoggingConfig()


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        path = _CONFIGS_DIR / "config.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)


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