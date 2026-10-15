"""E2E: LLM-команды не держат соединение из пула, а лимиты не обходятся."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from aiogram.methods import EditMessageText, SendMessage

from bot.infrastructure.aitunnel_client import AiTunnelClient, LlmResponse
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient
from tests.integration.fake_telegram import member
from tests.integration.helpers import CHAT, seed_users

AUTHOR, HERO = 801, 802
FANFIC = "Жили-были герои."


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def busy_connections(app_container, monkeypatch) -> list[int]:
    """Подменяет LLM: каждый вызов записывает, сколько соединений пула занято в этот момент."""
    db_pool = await app_container.get(asyncpg.Pool)
    seen: list[int] = []

    async def chat(self, messages, **kwargs):
        seen.append(db_pool.get_size() - db_pool.get_idle_size())
        return LlmResponse(text=FANFIC, input_tokens=10, output_tokens=5)

    monkeypatch.setattr(OpenAiClient, "chat", chat)
    monkeypatch.setattr(AiTunnelClient, "chat", chat)
    return seen


@pytest.fixture
async def heroes(conn):
    await seed_users(conn, AUTHOR, HERO)


def edits(tg) -> list[str]:
    return [request.text for request in tg.requests(EditMessageText)]


async def test_ff_waits_for_llm_without_holding_a_db_connection(tg, conn, heroes, busy_connections):
    await tg.send(member(AUTHOR), "/ff @user802 про кофе")

    # Раньше транзакция апдейта держала соединение всё время ожидания LLM:
    # пять параллельных /ff занимали весь пул, и бот вставал
    assert busy_connections == [0]
    assert FANFIC in edits(tg)
    assert await conn.fetchval("SELECT count(*) FROM llm_requests WHERE command = 'ff'") == 1


async def test_ff_daily_limit(tg, config, texts, heroes, busy_connections):
    limit = config.fanfic.daily_limit_per_user
    for _ in range(limit + 1):
        await tg.send(member(AUTHOR), "/ff @user802")
    assert len(busy_connections) == limit
    assert edits(tg)[-1] == texts._t["ff_rate_limit"].format(used=limit, limit=limit)


async def test_ff_daily_limit_skips_admins(tg, conn, config, busy_connections):
    admin = member(AUTHOR, username=config.admin.users[0])
    await seed_users(conn, HERO)
    for _ in range(config.fanfic.daily_limit_per_user + 1):
        await tg.send(admin, "/ff @user802")
    assert len(busy_connections) == config.fanfic.daily_limit_per_user + 1


async def test_second_llm_command_is_refused_while_the_first_runs(tg, texts, heroes, monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow_chat(self, messages, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        return LlmResponse(text=FANFIC, input_tokens=1, output_tokens=1)

    monkeypatch.setattr(OpenAiClient, "chat", slow_chat)
    first = asyncio.create_task(tg.send(member(AUTHOR), "/ff @user802"))
    await asyncio.wait_for(started.wait(), 5)
    try:
        # Раньше параллельные запросы проходили проверку лимита до того, как первый записался.
        # Без лока второй запрос повиснет в LLM — wait_for превращает это в падение теста
        await asyncio.wait_for(tg.send(member(AUTHOR), "/ff @user802"), 5)
    finally:
        finish.set()
        await first

    assert calls == 1
    assert [r.text for r in tg.requests(SendMessage)].count(texts._t["llm_busy"]) == 1
    # Ответ пришёл — слот свободен
    await tg.send(member(AUTHOR), "/ff @user802")
    assert calls == 2


async def test_llm_limit_counts_only_llm_and_search(tg, conn, config, heroes, busy_connections, monkeypatch):
    monkeypatch.setenv("AITUNNEL_API_KEY", "test")
    for _ in range(config.llm.daily_limit_per_user):
        await conn.execute(
            "INSERT INTO llm_requests (user_id, chat_id, command, query) VALUES ($1, $2, 'ff', '')", AUTHOR, CHAT
        )
    # Фанфики за сегодня не съедают лимит /llm (раньше считались все команды)
    await tg.send(member(AUTHOR), "/llm привет")
    assert busy_connections == [0]
    assert any(FANFIC in text for text in edits(tg))
