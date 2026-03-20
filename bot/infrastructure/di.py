"""DI-контейнер: провайдеры разбиты по доменам.

Для удобства реэкспортируются все провайдеры из одной точки.
"""

from bot.application.provider import AppServiceProvider
from bot.infrastructure.db.provider import DatabaseProvider
from bot.infrastructure.llm_provider import LlmProvider
from bot.infrastructure.redis.provider import RedisProvider
from bot.presentation.provider import PresentationProvider

__all__ = [
    "DatabaseProvider",
    "RedisProvider",
    "LlmProvider",
    "AppServiceProvider",
    "PresentationProvider",
]
