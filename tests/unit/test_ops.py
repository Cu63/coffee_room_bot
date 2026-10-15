"""Эксплуатация: пароль прокси не попадает в лог, healthcheck читает пульс бота."""

import asyncio
import os
import time

import pytest

from bot.infrastructure.heartbeat import heartbeat_loop, is_healthy
from bot.infrastructure.logger import mask_url_credentials


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        ("socks5://user:secret@proxy.local:1080", "socks5://user:***@proxy.local:1080"),
        ("http://user:p%40ss@10.0.0.1:3128/", "http://user:***@10.0.0.1:3128/"),
        ("http://proxy.local:3128", "http://proxy.local:3128"),
        ("socks5://user@proxy.local:1080", "socks5://user@proxy.local:1080"),
    ],
    ids=["socks5", "http-with-path", "no-credentials", "user-only"],
)
def test_mask_url_credentials(url, masked):
    assert mask_url_credentials(url) == masked


def test_health_follows_heartbeat_age(tmp_path):
    beat = tmp_path / "heartbeat"
    assert not is_healthy(beat)  # бот ещё не отметился
    beat.touch()
    assert is_healthy(beat)
    stale = time.time() - 300
    os.utime(beat, (stale, stale))
    assert not is_healthy(beat)  # цикл событий завис или база недоступна


class _Pool:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    async def fetchval(self, query):
        if not self.ok:
            raise ConnectionError("postgres down")
        return 1


class _Redis:
    async def ping(self):
        return True


@pytest.mark.parametrize("db_ok", [True, False], ids=["db-up", "db-down"])
async def test_heartbeat_beats_only_while_dependencies_answer(tmp_path, db_ok):
    beat = tmp_path / "heartbeat"
    task = asyncio.create_task(heartbeat_loop(_Pool(db_ok), _Redis(), beat))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert beat.exists() is db_ok
