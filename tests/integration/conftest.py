"""Интеграционные тесты: настоящие PostgreSQL и Redis.

Адреса берутся из TEST_DATABASE_URL и TEST_REDIS_URL; без них тесты пропускаются.
БД и номер Redis должны быть отдельными: перед каждым тестом все таблицы
очищаются, а Redis — FLUSHDB. Миграции применяются автоматически.

Локально, например:
    docker run -d --rm -p 5433:5432 -e POSTGRES_USER=scorebot -e POSTGRES_PASSWORD=scorebot \\
        -e POSTGRES_DB=scorebot_test postgres:16-alpine
    docker run -d --rm -p 6380:6379 redis:7-alpine
    TEST_DATABASE_URL=postgresql://scorebot:scorebot@localhost:5433/scorebot_test \\
    TEST_REDIS_URL=redis://localhost:6380/0 uv run pytest tests/integration
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import asyncpg
import pytest
import redis.asyncio as aioredis

from bot.application.score_service import ScoreService
from bot.domain.reaction_registry import ReactionRegistry
from bot.infrastructure.config_loader import load_config
from bot.infrastructure.db.postgres_daily_limits_repository import PostgresDailyLimitsRepository
from bot.infrastructure.db.postgres_event_repository import PostgresEventRepository
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from bot.infrastructure.db.postgres_per_target_limits_repository import PostgresPerTargetLimitsRepository
from bot.infrastructure.db.postgres_score_repository import PostgresScoreRepository
from bot.infrastructure.db.postgres_user_stats_repository import PostgresUserStatsRepository
from tests.integration.helpers import DB_URL, REDIS_URL

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _migration_version(path: Path) -> int:
    return int(re.match(r"V(\d+)__", path.name).group(1))


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Применить миграции к пустой тестовой БД (один раз за сессию)."""
    if not (DB_URL and REDIS_URL):
        return

    async def apply() -> None:
        conn = await asyncpg.connect(DB_URL)
        try:
            if await conn.fetchval("SELECT to_regclass('public.scores')") is None:
                for path in sorted(MIGRATIONS_DIR.glob("V*__*.sql"), key=_migration_version):
                    await conn.execute(path.read_text(encoding="utf-8"))
        finally:
            await conn.close()

    asyncio.run(apply())


@pytest.fixture(autouse=True)
def _require_services() -> None:
    if not (DB_URL and REDIS_URL):
        pytest.skip("TEST_DATABASE_URL / TEST_REDIS_URL не заданы — интеграционные тесты пропущены")


@pytest.fixture
async def pool():
    """Пул к тестовой БД; перед тестом все таблицы очищаются."""
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=8)
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'flyway_schema_history'"
        )
        await conn.execute(f"TRUNCATE {', '.join(t['tablename'] for t in tables)} RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest.fixture
async def conn(pool):
    async with pool.acquire() as c:
        yield c


@pytest.fixture
async def redis():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def score_service_factory(config):
    """ScoreService на настоящих репозиториях поверх переданного соединения."""

    def make(c: asyncpg.Connection) -> ScoreService:
        return ScoreService(
            score_repo=PostgresScoreRepository(c),
            event_repo=PostgresEventRepository(c),
            limits_repo=PostgresDailyLimitsRepository(c),
            message_repo=PostgresMessageRepository(c),
            reaction_registry=ReactionRegistry(config.reactions),
            per_target_limits_repo=PostgresPerTargetLimitsRepository(c),
            stats_repo=PostgresUserStatsRepository(c),
            self_reaction_allowed=config.self_reaction_allowed,
            daily_negative_given=config.limits.daily_negative_given,
            daily_positive_per_target=config.limits.daily_positive_per_target,
            daily_score_received=config.limits.daily_score_received,
            max_message_age_hours=config.limits.max_message_age_hours,
        )

    return make


@pytest.fixture
async def app_container(pool, redis, monkeypatch):
    """Полный DI-контейнер, как в main.py, поверх тестовых Postgres и Redis."""
    from dishka import make_async_container

    from bot.infrastructure.di import (
        AppServiceProvider,
        DatabaseProvider,
        LlmProvider,
        PresentationProvider,
        RedisProvider,
    )

    monkeypatch.setenv("DATABASE_URL", DB_URL)
    container = make_async_container(
        PresentationProvider(),
        DatabaseProvider(),
        RedisProvider(),
        LlmProvider(),
        AppServiceProvider(),
        context={aioredis.Redis: redis},
    )
    yield container
    await container.close()
