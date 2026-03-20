"""Обратная совместимость: все существующие импорты из этого модуля продолжают работать."""

from bot.infrastructure.redis.store import RedisStore

__all__ = ["RedisStore"]
