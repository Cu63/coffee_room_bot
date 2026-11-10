"""E2E /status: админ бота видит версию, базы и фоновые циклы; остальным — отказ."""

from __future__ import annotations

import re
import time

import pytest
from aiogram.methods import SendMessage

import bot.presentation.handlers.status as status_handler
from bot.infrastructure.background import LoopRegistry, LoopState
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.infrastructure.message_formatter import MessageFormatter
from tests.integration.fake_telegram import member
from tests.integration.helpers import seed_users

ADMIN, MEMBER = 801, 802


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def admin(app_container, conn) -> None:
    await seed_users(conn, ADMIN, MEMBER)
    (await app_container.get(AppConfig)).admin.users = [str(ADMIN)]


def last_reply(tg) -> str:
    return tg.requests(SendMessage)[-1].text


async def test_status_shows_version_databases_and_loops(tg, app_container, texts, admin):
    (await app_container.get(BotSettings)).bot_version = "82a3fab"
    loops = await app_container.get(LoopRegistry)
    now = time.time()
    loops.loops["unmute"] = LoopState(started_at=now - 600, last_pass=now - 12)
    loops.loops["lot"] = LoopState(
        started_at=now - 60, restarts=2, last_error_at=now - 30, last_error="TelegramBadRequest: <b>нет</b>"
    )

    await tg.send(member(ADMIN), "/status")

    text = last_reply(tg)
    assert "<code>82a3fab</code>" in text
    assert re.search(r"Postgres: ✅ \d+ мс", text) and re.search(r"Redis: ✅ \d+ мс", text)
    assert re.search(r"✅ unmute — 1[23]с назад", text)
    assert texts._t["status_loop_no_pass"].format(name="lot") + texts._t["status_loop_restarts"].format(count=2) in text
    # Текст ошибки — из исключения, его экранируют
    assert "TelegramBadRequest: &lt;b&gt;нет&lt;/b&gt;" in text
    assert text.index("lot") < text.index("unmute")  # по алфавиту


async def test_status_reports_a_database_that_does_not_answer(tg, monkeypatch, admin):
    async def refused(pool, timeout=0.0) -> float:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(status_handler, "ping_postgres", refused)
    await tg.send(member(ADMIN), "/status")

    text = last_reply(tg)
    assert "Postgres: ❌ не отвечает (ConnectionRefusedError)" in text
    assert "Redis: ✅" in text


async def test_status_without_loops_and_version(tg, app_container, texts, admin):
    (await app_container.get(BotSettings)).bot_version = ""
    await tg.send(member(ADMIN), "/status")

    text = last_reply(tg)
    assert f"<code>{texts._t['status_version_unknown']}</code>" in text
    assert text.endswith(texts._t["status_no_loops"])


async def test_status_is_for_bot_admins(tg, texts, admin):
    await tg.send(member(MEMBER), "/status")
    assert last_reply(tg) == texts._t["admin_not_allowed"]
