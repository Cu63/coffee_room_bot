"""E2E раундов /quiz и /whosaid: одна попытка, приз первому верному из кассы бота, один раунд на чат."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta

import pytest
from aiogram.methods import EditMessageText, SendMessage

import bot.infrastructure.round_loop as round_loop
from bot.domain.tz import TZ_MSK
from bot.infrastructure.aitunnel_client import LlmResponse
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient
from bot.infrastructure.redis_store import RedisStore
from tests.integration.fake_telegram import member
from tests.integration.helpers import BOT_ID, CHAT, balance, seed_users

HOST, ANNA, BORIS, VERA = 791, 792, 793, 794
QUIZ = {
    "question": "Столица <Франции>?",
    "options": ["Париж", "Лион", "Ницца", "Марсель"],
    "answer": 0,
    "explanation": "Париж — столица с X века.",
}


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def store(app_container) -> RedisStore:
    return await app_container.get(RedisStore)


@pytest.fixture
async def players(conn) -> None:
    await seed_users(conn, HOST, ANNA, BORIS, VERA)
    await seed_users(conn, BOT_ID, balance=100, is_bot=True)  # касса бота


@pytest.fixture
def llm(monkeypatch) -> list[dict]:
    """Подменяет LLM: отдаёт вопрос QUIZ (или то, что положат в answers) и запоминает запросы."""
    calls: list[dict] = []
    answers: list[str] = []

    async def chat(self, messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return LlmResponse(text=answers.pop(0) if answers else json.dumps(QUIZ), input_tokens=10, output_tokens=5)

    monkeypatch.setattr(OpenAiClient, "chat", chat)
    calls.append({"answers": answers})  # первый элемент — очередь ответов для теста
    return calls


def posted(tg) -> tuple[int, dict[str, str]]:
    """Последний вопрос раунда: id сообщения и кнопки {подпись: callback_data}."""
    for request in reversed(tg.session.log):
        method, result = request
        markup = getattr(method, "reply_markup", None)
        if isinstance(method, SendMessage | EditMessageText) and markup is not None:
            message_id = method.message_id if isinstance(method, EditMessageText) else result.message_id
            return message_id, {b.text: b.callback_data for row in markup.inline_keyboard for b in row}
    raise AssertionError("вопрос раунда не опубликован")


def last_text(tg) -> str:
    return tg.requests(EditMessageText)[-1].text


async def test_quiz_first_right_answer_gets_the_prize(tg, conn, texts, config, players, llm):
    await tg.send(member(HOST), "/quiz кино")

    [_, call] = llm
    assert call["json_mode"] is True and "Тема: кино" in call["messages"][1]["content"]
    message_id, buttons = posted(tg)
    assert sorted(buttons) == sorted(QUIZ["options"])
    assert "Столица &lt;Франции&gt;?" in tg.requests(EditMessageText)[-1].text
    wrong = next(data for name, data in buttons.items() if name != "Париж")

    await tg.click(member(ANNA), message_id, wrong)
    await tg.click(member(ANNA), message_id, buttons["Париж"])  # попытка была одна
    await tg.click(member(BORIS), message_id, buttons["Париж"])
    await tg.click(member(VERA), message_id, buttons["Париж"])

    assert tg.answers() == [
        texts._t["round_wrong"],
        texts._t["round_already_answered"],
        texts._t["round_right"],
        texts._t["round_over"],
    ]
    prize = config.quiz.prize
    assert (await balance(conn, BORIS), await balance(conn, BOT_ID), await balance(conn, ANNA)) == (
        prize,
        100 - prize,
        0,
    )
    result = last_text(tg)
    assert "<code>@user793</code>: <b>Париж</b>" in result and "Париж — столица" in result
    assert tg.requests(EditMessageText)[-1].reply_markup is None


async def test_simultaneous_right_answers_pay_once(tg, conn, config, players, llm, monkeypatch):
    await tg.send(member(HOST), "/quiz")
    message_id, buttons = posted(tg)

    # Оба прочитали раунд до того, как кто-то его забрал
    both_read = asyncio.Barrier(2)
    original_get = RedisStore.round_get

    async def get_together(self, round_id):
        data = await original_get(self, round_id)
        await asyncio.wait_for(both_read.wait(), 5)
        return data

    monkeypatch.setattr(RedisStore, "round_get", get_together)
    await asyncio.gather(
        tg.click(member(ANNA), message_id, buttons["Париж"]), tg.click(member(BORIS), message_id, buttons["Париж"])
    )

    prize = config.quiz.prize
    assert await balance(conn, BOT_ID) == 100 - prize
    assert sorted([await balance(conn, ANNA), await balance(conn, BORIS)]) == [0, prize]


async def test_empty_house_means_a_win_without_prize(tg, conn, texts, players, llm):
    await seed_users(conn, BOT_ID, balance=0, is_bot=True)  # касса пуста
    await tg.send(member(HOST), "/quiz")
    message_id, buttons = posted(tg)

    await tg.click(member(ANNA), message_id, buttons["Париж"])

    assert texts._t["round_no_prize"] in last_text(tg)
    assert (await balance(conn, ANNA), await balance(conn, BOT_ID)) == (0, 0)


async def test_one_round_per_chat_then_a_pause(tg, texts, players, llm):
    await tg.send(member(HOST), "/quiz")
    await tg.send(member(ANNA), "/whosaid")
    assert tg.requests(SendMessage)[-1].text == texts._t["round_busy"]

    message_id, buttons = posted(tg)
    await tg.click(member(BORIS), message_id, buttons["Париж"])
    await tg.send(member(ANNA), "/quiz")

    assert tg.requests(SendMessage)[-1].text.startswith("⏳")
    assert len(llm) == 2  # второй вопрос у модели не просили


async def test_bad_model_answer_frees_the_chat(tg, texts, players, llm):
    llm[0]["answers"].append("это не json")
    await tg.send(member(HOST), "/quiz")
    assert last_text(tg) == texts._t["quiz_error"]

    await tg.send(member(ANNA), "/quiz")  # чат свободен, паузы нет: раунд не начался
    assert len(llm) == 3
    posted(tg)


async def test_unanswered_round_reveals_the_answer(tg, app_container, texts, store, players, llm):
    await tg.send(member(HOST), "/quiz")
    [round_id] = {data.split(":")[1] for data in posted(tg)[1].values()}
    data = await store.round_get(round_id)
    await store.round_save(data | {"expires_at": time.time() - 1}, 60)

    await round_loop._tick(tg.bot, app_container)

    assert last_text(tg).endswith(
        texts._t["round_expired"].format(title=data["title"], question=data["question"], answer="Париж")
        + texts._t["round_explanation"].format(text=data["explanation"])
    )
    assert await store.round_get(round_id) is None
    assert await store.round_claim_chat(CHAT, "next", 60)  # чат свободен


async def say(conn, user_id: int, message_id: int, text: str) -> None:
    await conn.execute(
        "INSERT INTO messages (message_id, chat_id, user_id, sent_at, text) VALUES ($1, $2, $3, $4, $5)",
        message_id,
        CHAT,
        user_id,
        datetime.now(TZ_MSK) - timedelta(hours=1),
        text,
    )


async def test_whosaid_guess_the_author(tg, conn, texts, config, players):
    await say(conn, ANNA, 1, "кто съел <b>последний</b> круассан?!")  # единственная подходящая цитата
    for message_id, user_id in enumerate((BORIS, VERA, HOST), start=2):
        await say(conn, user_id, message_id, "ок")

    await tg.send(member(BORIS), "/whosaid")

    message_id, buttons = posted(tg)
    assert sorted(buttons) == ["@user791", "@user792", "@user793", "@user794"]
    assert "«кто съел &lt;b&gt;последний&lt;/b&gt; круассан?!»" in tg.requests(SendMessage)[-1].text

    await tg.click(member(ANNA), message_id, buttons["@user792"])  # автор не отвечает
    await tg.click(member(VERA), message_id, buttons["@user792"])

    assert tg.answers() == [texts._t["whosaid_own_quote"], texts._t["round_right"]]
    assert await balance(conn, VERA) == config.whosaid.prize


async def test_whosaid_needs_four_authors(tg, conn, texts, store, players):
    await say(conn, ANNA, 1, "кто съел последний круассан?!")
    await say(conn, BORIS, 2, "ок")

    await tg.send(member(VERA), "/whosaid")

    assert tg.requests(SendMessage)[-1].text == texts._t["whosaid_not_enough"]
    assert await store.round_claim_chat(CHAT, "next", 60)  # чат свободен
