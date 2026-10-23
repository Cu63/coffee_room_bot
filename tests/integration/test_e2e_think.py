"""E2E: /think — мысль по IQ участника и свежей переписке, не больше двух в день."""

from __future__ import annotations

import asyncpg
import pytest
from aiogram.methods import EditMessageText

from bot.infrastructure.aitunnel_client import LlmResponse
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.db.postgres_iq_repository import PostgresIqRepository
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient
from tests.integration.fake_telegram import member
from tests.integration.helpers import CHAT, seed_users

THINKER, TALKER = 741, 742
THOUGHT = "Интересно, <кофе> & чай — это одно и то же?"


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def llm_calls(app_container, monkeypatch) -> list[dict]:
    """Подменяет LLM: запоминает запрос и сколько соединений пула занято в момент вызова."""
    db_pool = await app_container.get(asyncpg.Pool)
    calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        calls.append({"messages": messages, "busy": db_pool.get_size() - db_pool.get_idle_size(), **kwargs})
        return LlmResponse(text=THOUGHT, input_tokens=10, output_tokens=5)

    monkeypatch.setattr(OpenAiClient, "chat", chat)
    return calls


def answers(tg) -> list[str]:
    return [request.text for request in tg.requests(EditMessageText)]


async def test_thought_follows_iq_and_recent_messages(tg, conn, texts, llm_calls, config):
    await seed_users(conn, THINKER, TALKER)
    await PostgresIqRepository(conn).set_iq(THINKER, CHAT, 150)
    await tg.send(member(TALKER), "кто сломал прод в пятницу?")
    await tg.send(member(THINKER), "точно не я, я пил кофе")

    await tg.send(member(THINKER), "/think")

    [call] = llm_calls
    system, user = (m["content"] for m in call["messages"])
    assert system == texts._t["think_system_prompt"]
    assert "IQ участника: 150" in user and texts._t["think_style_genius"] in user  # IQ задаёт уровень мысли
    assert "@user742: кто сломал прод в пятницу?" in user  # тема — из свежей переписки
    assert "@user741: точно не я, я пил кофе" in user
    assert (call["model"], call["max_tokens"]) == (config.think.model, config.think.max_output_tokens)
    assert call["busy"] == 0  # соединение из пула не держим, пока ждём LLM

    reply = answers(tg)[-1]
    assert "<code>@user741</code></b> · IQ 150" in reply
    assert "<i>Интересно, &lt;кофе&gt; &amp; чай — это одно и то же?</i>" in reply  # текст модели — только текст
    assert "Осталось мыслей на сегодня: 1/2" in reply
    assert await conn.fetchval("SELECT count(*) FROM llm_requests WHERE command = 'think'") == 1


async def test_default_iq_thinks_like_an_average_person(tg, conn, texts, llm_calls, app_container):
    await seed_users(conn, THINKER)
    default_iq = (await app_container.get(AppConfig)).iq.default

    await tg.send(member(THINKER), "/think")

    user_prompt = llm_calls[0]["messages"][1]["content"]
    assert f"IQ участника: {default_iq}" in user_prompt
    assert texts._t["think_style_average"] in user_prompt
    assert texts._t["think_no_context"] in user_prompt  # в чате ещё никто ничего не писал


async def test_two_thoughts_a_day(tg, conn, texts, llm_calls):
    await seed_users(conn, THINKER)
    for _ in range(3):
        await tg.send(member(THINKER), "/think")

    assert len(llm_calls) == 2  # третий раз модель даже не спрашиваем
    assert "Осталось мыслей на сегодня: 0/2" in answers(tg)[1]
    assert answers(tg)[2] == texts._t["think_rate_limit"].format(used=2, limit=2)


async def test_admins_think_without_limit(tg, conn, config, texts, llm_calls):
    admin = member(THINKER, username=config.admin.users[0])
    await seed_users(conn, THINKER)
    for _ in range(3):
        await tg.send(admin, "/think")

    assert len(llm_calls) == 3
    assert texts._t["think_left_admin"].format(used=3) in answers(tg)[-1]
