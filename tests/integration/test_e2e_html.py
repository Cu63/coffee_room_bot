"""E2E: чужой текст (ответ LLM, /anon, лента новостей) не ломает HTML сообщения и не протаскивает теги."""

from __future__ import annotations

import time

from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Message, Update

import bot.presentation.handlers.news as news_module
from bot.infrastructure.aitunnel_client import AiTunnelClient, LlmResponse
from bot.infrastructure.config_loader import BotSettings
from bot.infrastructure.news_fetcher import NewsItem
from bot.infrastructure.redis_store import RedisStore
from tests.integration.fake_telegram import member
from tests.integration.helpers import CHAT, balance, seed_users

USER = 751


async def test_llm_answer_is_sanitized(tg, conn, app_container, monkeypatch):
    await seed_users(conn, USER)
    (await app_container.get(BotSettings)).aitunnel_api_key = "test"

    async def chat(self, messages, **kwargs):
        return LlmResponse(
            text="a < b & **жирно** <script>alert(1)</script> [ссылка](https://example.com) <b>не закрыт",
            input_tokens=1,
            output_tokens=1,
        )

    monkeypatch.setattr(AiTunnelClient, "chat", chat)
    await tg.send(member(USER), "/llm вопрос")

    # Раньше «<» в ответе ломал HTML, и ответ уходил текстом без форматирования
    answer = tg.requests(EditMessageText)[-1].text
    assert answer.startswith("a &lt; b &amp; <b>жирно</b> alert(1)")
    assert "<script>" not in answer
    assert '<a href="https://example.com">ссылка</a>' in answer
    assert "<b>не закрыт</b>\n\n<i>Осталось запросов" in answer  # незакрытый тег не глотает подпись


async def test_anon_text_is_escaped(tg, conn, app_container):
    await seed_users(conn, USER, balance=500)
    store = await app_container.get(RedisStore)
    await store.anon_set_state(USER, {"step": "write_message", "chat_id": CHAT, "chat_title": "Кофе <3"})

    private = Message.model_validate(
        {
            "message_id": 1,
            "date": int(time.time()),
            "chat": {"id": USER, "type": "private", "first_name": "User"},
            "from": member(USER).model_dump(mode="json", exclude_none=True),
            "text": "a < b & c",
        }
    )
    await tg.dp.feed_update(tg.bot, Update(update_id=10_000, message=private))

    # Раньше текст шёл в HTML как есть: «a < b» ломало отправку, а теги автора проходили в чат
    to_chat, confirmation = tg.requests(SendMessage)
    assert (to_chat.chat_id, to_chat.text.split("\n\n", 1)[1]) == (CHAT, "a &lt; b &amp; c")
    assert "<b>Кофе &lt;3</b>" in confirmation.text
    assert await balance(conn, USER) == 400


async def test_news_fields_are_escaped(tg, conn, monkeypatch):
    await seed_users(conn, USER)

    async def fetch(**kwargs):
        return [
            NewsItem(title="AT&T <купила> всех", url="https://x.ru/?a=1&b=2", source="Хабр & Ко", description="a < b")
        ]

    monkeypatch.setattr(news_module, "fetch_random_news", fetch)
    await tg.send(member(USER), "/news")

    text = tg.requests(EditMessageText)[-1].text
    assert "<b>AT&amp;T &lt;купила&gt; всех</b>" in text
    assert "a &lt; b" in text and "Хабр &amp; Ко" in text
    assert 'href="https://x.ru/?a=1&amp;b=2"' in text
