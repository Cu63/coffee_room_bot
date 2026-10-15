"""Общие константы и хелперы интеграционных тестов."""

from __future__ import annotations

import os

import asyncpg

from bot.domain.entities import User
from bot.domain.ledger import LedgerKind
from bot.infrastructure.db.postgres_score_repository import PostgresScoreRepository
from bot.infrastructure.db.postgres_user_repository import PostgresUserRepository

DB_URL = os.getenv("TEST_DATABASE_URL")
REDIS_URL = os.getenv("TEST_REDIS_URL")

CHAT = -1001234567890
BOT_ID = 7_000_000_001


async def seed_users(
    c: asyncpg.Connection, *user_ids: int, balance: int | None = None, chat_id: int = CHAT, is_bot: bool = False
) -> None:
    """Завести пользователей (FK) и, если задано, выставить баланс в чате."""
    users = PostgresUserRepository(c)
    for uid in user_ids:
        await users.upsert(User(id=uid, username=f"user{uid}", full_name=f"User {uid}", is_bot=is_bot))
        if balance is not None:
            await PostgresScoreRepository(c).set_value(uid, chat_id, balance, kind=LedgerKind.OPENING)


async def balance(c: asyncpg.Connection, user_id: int, chat_id: int = CHAT) -> int:
    return await c.fetchval("SELECT value FROM scores WHERE user_id = $1 AND chat_id = $2", user_id, chat_id) or 0
