"""RedisProvider — клиент и store.

Redis-клиент создаётся один раз в main() и передаётся в контейнер
через context, чтобы не плодить дублирующий пул соединений.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from dishka import Provider, Scope, from_context, provide

from bot.infrastructure.redis.store import RedisStore


class RedisProvider(Provider):

    # Получаем уже созданный клиент из контекста контейнера (main.py)
    redis = from_context(provides=aioredis.Redis, scope=Scope.APP)

    @provide(scope=Scope.APP)
    def get_redis_store(self, redis: aioredis.Redis) -> RedisStore:
        return RedisStore(redis)
