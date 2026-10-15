"""Пульс бота для healthcheck Docker.

Цикл раз в 30 секунд проверяет Postgres и Redis и отмечается в файле. Healthcheck
(`python -m bot.infrastructure.heartbeat`) считает бота здоровым, пока отметка свежее
двух минут: зависший цикл событий или пропавшая база делают контейнер unhealthy,
и deploy.sh видит, что новая версия не поднялась.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = Path(os.getenv("HEARTBEAT_FILE", "/tmp/bot-heartbeat"))
INTERVAL = 30
MAX_AGE = 120


async def heartbeat_loop(pool: asyncpg.Pool, redis: aioredis.Redis, path: Path = HEARTBEAT_FILE) -> None:
    while True:
        try:
            await pool.fetchval("SELECT 1")
            await redis.ping()
            path.touch()
        except Exception:
            logger.warning("heartbeat: Postgres или Redis не отвечает", exc_info=True)
        await asyncio.sleep(INTERVAL)


def is_healthy(path: Path = HEARTBEAT_FILE, max_age: float = MAX_AGE) -> bool:
    try:
        return time.time() - path.stat().st_mtime < max_age
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    sys.exit(0 if is_healthy() else 1)
