"""RedisProvider — клиент и store."""

from __future__ import annotations

from collections.abc import AsyncIterable

import redis.asyncio as aioredis
from dishka import Provider, Scope, provide

from bot.infrastructure.config_loader import BotSettings
from bot.infrastructure.redis.store import RedisStore


class RedisProvider(Provider):

    @provide(scope=Scope.APP)
    async def get_redis(self, settings: BotSettings) -> AsyncIterable[aioredis.Redis]:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        yield r
        await r.aclose()

    @provide(scope=Scope.APP)
    def get_redis_store(self, redis: aioredis.Redis) -> RedisStore:
        return RedisStore(redis)
