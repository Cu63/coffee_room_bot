"""Транзакция запроса: что коммитится, что откатывается, и хендлер не перезапускается."""

from __future__ import annotations

import contextlib
import datetime

import asyncpg
import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.aiogram import FromDishka, inject, setup_dishka

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.domain.ledger import LedgerKind
from bot.infrastructure.config_loader import DatabaseConfig
from bot.infrastructure.db.provider import DatabaseProvider
from bot.presentation.errors import AbortUpdate
from bot.presentation.middlewares.telegram_errors import TelegramErrorsMiddleware
from tests.integration.helpers import CHAT, DB_URL, balance, seed_users

A = 201
METHOD = SendMessage(chat_id=CHAT, text="x")


class _DbConfig(Provider):
    @provide(scope=Scope.APP)
    def db(self) -> DatabaseConfig:
        return DatabaseConfig(dsn=DB_URL)


@pytest.fixture
async def container(pool):
    c = make_async_container(DatabaseProvider(), _DbConfig())
    yield c
    await c.close()


@pytest.mark.parametrize(
    ("exc", "committed"),
    [
        (None, True),
        (ValueError("bug"), False),
        (AbortUpdate(), False),
        (TelegramNetworkError(method=METHOD, message="Request timeout error"), True),
        (TelegramBadRequest(method=METHOD, message="message is not modified"), True),
    ],
)
async def test_commit_or_rollback_by_exception(container, conn, exc, committed):
    await seed_users(conn, A, balance=100)
    with pytest.raises(type(exc)) if exc else contextlib.nullcontext():
        async with container() as request:
            await (await request.get(IScoreRepository)).add_delta(A, CHAT, -10, kind=LedgerKind.ADMIN)
            if exc is not None:
                raise exc
    assert await balance(conn, A) == (90 if committed else 100)


@pytest.mark.parametrize(("text", "expected_balance"), [("ok", 90), ("network", 90), ("abort", 100)])
async def test_handler_runs_once_and_errors_are_absorbed(container, conn, text, expected_balance):
    await seed_users(conn, A, balance=100)
    calls: list[str] = []
    router = Router()

    @router.message()
    @inject
    async def handler(message: Message, repo: FromDishka[IScoreRepository]) -> None:
        calls.append(message.text)
        await repo.add_delta(A, CHAT, -10, kind=LedgerKind.ADMIN)
        if message.text == "network":
            raise TelegramNetworkError(method=METHOD, message="Request timeout error")
        if message.text == "abort":
            raise AbortUpdate

    dp = Dispatcher()
    dp.message.outer_middleware(TelegramErrorsMiddleware())  # как в bootstrap: снаружи dishka
    dp.include_router(router)
    setup_dishka(container, dp)

    bot = Bot(token="42:TEST")
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.datetime.now(),
            text=text,
            chat=Chat(id=CHAT, type="supergroup"),
            from_user=User(id=A, is_bot=False, first_name="A"),
        ),
    )
    await dp.feed_update(bot, update)  # исключения гасит TelegramErrorsMiddleware
    await bot.session.close()

    assert calls == [text]  # раньше при сетевой ошибке хендлер запускался до трёх раз
    assert await balance(conn, A) == expected_balance


def in_use(db_pool: asyncpg.Pool) -> int:
    return db_pool.get_size() - db_pool.get_idle_size()


async def test_connection_is_taken_on_first_query(container):
    db_pool = await container.get(asyncpg.Pool)
    async with container() as request:
        repo = await request.get(IScoreRepository)
        assert in_use(db_pool) == 0  # репозиторий есть, запросов ещё не было
        await repo.get(A, CHAT)
        assert in_use(db_pool) == 1
    assert in_use(db_pool) == 0


async def test_checkpoint_commits_and_lets_the_connection_go(container, conn):
    await seed_users(conn, A, balance=100)
    db_pool = await container.get(asyncpg.Pool)
    with pytest.raises(ValueError):
        async with container() as request:
            repo = await request.get(IScoreRepository)
            await repo.add_delta(A, CHAT, -10, kind=LedgerKind.ADMIN)
            await (await request.get(ITransactionManager)).checkpoint()
            assert in_use(db_pool) == 0  # пока ждём LLM, соединение в пуле
            await repo.add_delta(A, CHAT, -1, kind=LedgerKind.ADMIN)  # тот же репозиторий открывает новую транзакцию
            raise ValueError
    assert await balance(conn, A) == 90  # до checkpoint — зафиксировано, после — откачено


async def test_savepoint_keeps_the_transaction_alive(app_container, conn):
    """Упавшая необязательная запись откатывает только себя: приз до неё остаётся."""
    await seed_users(conn, 41)
    async with app_container() as scope:
        tx = await scope.get(ITransactionManager)
        scores = await scope.get(IScoreRepository)
        await scores.add_delta(41, CHAT, 10, kind=LedgerKind.PRIZE, ref="wordgame:1")
        with pytest.raises(asyncpg.DataError):
            async with tx.savepoint():
                await tx.get_connection().execute("SELECT 1 / 0")
        await scores.add_delta(41, CHAT, 5, kind=LedgerKind.BONUS, ref="after")

    assert await balance(conn, 41) == 15
