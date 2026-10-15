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
from aiogram import Bot, Dispatcher
from dishka.integrations.aiogram import setup_dishka

from bot.application.score_service import ScoreService
from bot.domain.reaction_registry import ReactionRegistry
from bot.infrastructure.bootstrap import (
    register_bot_user,
    register_middlewares,
    register_post_dishka_middlewares,
    register_routers,
)
from bot.infrastructure.config_loader import AppConfig, BotSettings, load_config
from bot.infrastructure.db.postgres_daily_limits_repository import PostgresDailyLimitsRepository
from bot.infrastructure.db.postgres_event_repository import PostgresEventRepository
from bot.infrastructure.db.postgres_ledger_repository import PostgresLedgerRepository
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from bot.infrastructure.db.postgres_per_target_limits_repository import PostgresPerTargetLimitsRepository
from bot.infrastructure.db.postgres_score_repository import PostgresScoreRepository
from bot.infrastructure.db.postgres_user_stats_repository import PostgresUserStatsRepository
from tests.integration.fake_telegram import BOT_USER, FakeTelegramSession, TelegramHarness
from tests.integration.helpers import BOT_ID, DB_URL, REDIS_URL

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _migration_version(path: Path) -> int:
    return int(re.match(r"V(\d+)__", path.name).group(1))


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Применить миграции к тестовой БД с нуля (один раз за сессию)."""
    if not (DB_URL and REDIS_URL):
        return

    async def apply() -> None:
        conn = await asyncpg.connect(DB_URL)
        try:
            # Схему пересоздаём на каждый прогон, чтобы локальная БД не отставала от новых
            # миграций. Каждая миграция — в своей транзакции, как у Flyway.
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            for path in sorted(MIGRATIONS_DIR.glob("V*__*.sql"), key=_migration_version):
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
        finally:
            await conn.close()

    asyncio.run(apply())


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "ledger_tampering: тест сам ломает журнал операций — общая проверка журнала после него не нужна"
    )


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


@pytest.fixture(autouse=True)
async def _ledger_is_consistent(request, _require_services, pool):
    """После каждого теста: всё, что меняло баланс, записано в журнал с типом операции."""
    yield
    if request.node.get_closest_marker("ledger_tampering"):
        return
    async with pool.acquire() as c:
        unknown = await c.fetch("SELECT chat_id, user_id, delta FROM ledger WHERE kind = 'unknown'")
        assert not unknown, f"баланс менялся мимо репозитория, без типа операции: {[dict(r) for r in unknown]}"
        mismatches = await PostgresLedgerRepository(c).mismatches()
        assert not mismatches, f"баланс не сходится с журналом: {mismatches}"


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


@pytest.fixture
async def tg(app_container):
    """Бот целиком: Dispatcher с middleware и роутерами, как в main.py; Telegram — фейковый."""
    config = await app_container.get(AppConfig)
    config.auto_react.enabled = False  # случайная реакция бота начисляла бы автору баллы
    await register_bot_user(app_container, BOT_USER)

    dp = Dispatcher()
    register_middlewares(dp, BOT_USER)
    register_routers(dp, config, BotSettings(_env_file=None))
    setup_dishka(app_container, dp)
    register_post_dishka_middlewares(dp, BOT_USER)
    session = FakeTelegramSession()
    yield TelegramHarness(dp, Bot(token=f"{BOT_ID}:TEST", session=session), session)

    # Роутеры хендлеров — синглтоны модулей: отцепляем их, чтобы следующий тест собрал свой Dispatcher
    for router in dp.sub_routers:
        router._parent_router = None
    dp.sub_routers.clear()
