"""Задержка ответа Postgres и Redis для /status."""

from __future__ import annotations

import asyncio
import time

import asyncpg
import redis.asyncio as aioredis

TIMEOUT = 3.0  # дольше — считаем, что не отвечает


async def ping_postgres(pool: asyncpg.Pool, timeout: float = TIMEOUT) -> float:
    """Секунды до ответа на SELECT 1 через соединение из пула: занятый пул тоже виден."""
    started = time.perf_counter()
    async with asyncio.timeout(timeout):
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    return time.perf_counter() - started


async def ping_redis(redis: aioredis.Redis, timeout: float = TIMEOUT) -> float:
    """Секунды до ответа на PING."""
    started = time.perf_counter()
    async with asyncio.timeout(timeout):
        await redis.ping()  # pyright: ignore[reportGeneralTypeIssues] — redis-py: «Awaitable[bool] | bool»
    return time.perf_counter() - started
