"""E2E записей о предупреждениях в трекере.

Каждое выданное /warn уходит записью в топик warn трекера, к которому привязан чат: то же, что увидел
чат (там ответ бота и команда удаляются через минуту), и откуда оно. Топик назначают /settopic warn;
без него записи нет — в общий чат трекера тоже. Под записью кнопка «Снять предупреждение»: снимает
именно это предупреждение тот, кто может /unwarn в чате, где оно выдано.
"""

from __future__ import annotations

from contextlib import suppress

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageReplyMarkup, EditMessageText, SendMessage

import bot.presentation.handlers.warn as warn_handlers
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from tests.integration.fake_telegram import member
from tests.integration.helpers import CHAT, seed_users

MOD, MOD2, TARGET, MEMBER = 791, 792, 793, 794
TRACKER, TOPIC = -1009876543210, 77
SOURCE = f"Чат: Кофейня · выдал <code>@user{MOD}</code>"


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def store(app_container) -> RedisStore:
    return await app_container.get(RedisStore)


@pytest.fixture
async def chat(tg, conn, store) -> None:
    """Чат привязан к трекеру; топик warn назначает фикстура warn_topic."""
    await seed_users(conn, MOD, MOD2, TARGET, MEMBER)
    tg.session.set_admin(MOD, can_restrict_members=True)
    tg.session.set_admin(MOD2, can_restrict_members=True)
    await store.tracker_set_source(CHAT, TRACKER)


@pytest.fixture
async def warn_topic(store) -> None:
    await store.tracker_set_topic(TRACKER, "warn", TOPIC)


def in_chat(tg) -> list[str]:
    return [request.text for request in tg.requests(SendMessage) if request.chat_id == CHAT]


def in_tracker(tg) -> list[SendMessage]:
    return [request for request in tg.requests(SendMessage) if request.chat_id == TRACKER]


def remove_button(entry: SendMessage) -> str:
    assert entry.reply_markup is not None
    [[button]] = entry.reply_markup.inline_keyboard
    return button.callback_data


async def warnings(conn) -> list[str]:
    rows = await conn.fetch("SELECT reason FROM warnings WHERE user_id = $1 ORDER BY id", TARGET)
    return [row["reason"] for row in rows]


async def test_warn_goes_to_the_warn_topic(tg, chat, warn_topic):
    await tg.send(member(MOD), f"/warn @user{TARGET} флуд <b>капсом</b>")

    [entry] = in_tracker(tg)
    assert entry.message_thread_id == TOPIC
    # То же, что увидел чат, и откуда оно; причина экранирована, как и в чате
    assert entry.text == in_chat(tg)[-1] + "\n\n" + SOURCE
    assert "флуд &lt;b&gt;капсом&lt;/b&gt;" in entry.text


async def test_warn_by_reply_links_the_offending_message(tg, chat, warn_topic):
    command_id = await tg.send(member(MOD), "/warn флуд", reply_to=member(TARGET))

    # Ссылка на сообщение нарушителя, а не на команду: команду бот удалит через минуту
    offending_id = command_id + 1
    link = f' · <a href="https://t.me/c/1234567890/{offending_id}">сообщение</a>'
    assert in_tracker(tg)[-1].text == in_chat(tg)[-1] + "\n\n" + SOURCE + link

    # Цель названа ником: сообщение, на которое ответили, — не её, ссылки нет
    await tg.send(member(MOD), f"/warn @user{TARGET} ещё", reply_to=member(MEMBER))
    assert in_tracker(tg)[-1].text.endswith(SOURCE)


async def test_without_the_warn_topic_nothing_goes_to_the_tracker(tg, store, chat):
    await store.tracker_set_topic(TRACKER, "feature", 5)  # чужие топики не в счёт

    await tg.send(member(MOD), f"/warn @user{TARGET}")

    assert in_tracker(tg) == []
    assert len(in_chat(tg)) == 1


async def test_settopic_warn(tg, store, chat):
    # /settopic пишут внутри топика трекера; трекером здесь служит сам чат
    await tg.send(member(MOD), "/settopic warn", thread_id=TOPIC)

    assert await store.tracker_get_topic(CHAT, "warn") == TOPIC


async def test_tracker_failure_keeps_the_warning(tg, conn, monkeypatch, chat, warn_topic):
    async def redis_down(self, *args):
        raise ConnectionError("redis down")

    monkeypatch.setattr(RedisStore, "tracker_get_topic", redis_down)
    await tg.send(member(MOD), f"/warn @user{TARGET} флуд")

    # Запись — о том, что уже случилось: её сбой не откатывает само предупреждение
    assert await warnings(conn) == ["флуд"]
    assert in_tracker(tg) == []


async def test_button_removes_exactly_that_warning(tg, conn, texts, config, chat, warn_topic):
    t = texts._t
    await tg.send(member(MOD), f"/warn @user{TARGET} первое")
    await tg.send(member(MOD), f"/warn @user{TARGET} второе")
    first = in_tracker(tg)[0]

    await tg.click(member(MOD2), tg.result(first).message_id, remove_button(first))

    # Снято то, под чьей записью нажали, а не последнее, как у /unwarn
    assert await warnings(conn) == ["второе"]
    assert tg.answers() == [t["warn_removed_answer"]]
    [edit] = tg.requests(EditMessageText)
    removed = t["warn_removed"].format(moderator=f"<code>@user{MOD2}</code>", count=1, limit=config.warn.limit)
    assert edit.text.endswith("\n" + removed)
    assert edit.reply_markup is None


async def test_button_needs_mute_rights_in_the_warned_chat(tg, conn, texts, chat, warn_topic):
    await tg.send(member(MOD), f"/warn @user{TARGET} флуд")
    entry = in_tracker(tg)[0]

    await tg.click(member(MEMBER), tg.result(entry).message_id, remove_button(entry))

    assert tg.answers() == [texts._t["mute_not_allowed"]]
    assert await warnings(conn) == ["флуд"]


async def test_button_of_a_warning_that_is_gone(tg, texts, chat, warn_topic):
    await tg.send(member(MOD), f"/warn @user{TARGET} флуд")
    entry = in_tracker(tg)[0]
    await tg.send(member(MOD), f"/unwarn @user{TARGET}")  # его уже сняли командой

    await tg.click(member(MOD), tg.result(entry).message_id, remove_button(entry))

    assert tg.answers() == [texts._t["warn_remove_gone"]]
    # Кнопку, которой нечего снимать, бот убирает
    assert tg.requests(EditMessageReplyMarkup)[-1].reply_markup is None


async def test_burned_warnings_have_no_button(tg, texts, config, chat, warn_topic):
    for _ in range(config.warn.limit):
        await tg.send(member(MOD), f"/warn @user{TARGET}")

    entries = in_tracker(tg)
    # Лимит — автомут, предупреждения сгорели: в последней записи снимать нечего
    assert [entry.reply_markup is not None for entry in entries] == [True] * (config.warn.limit - 1) + [False]
    assert texts._t["warn_muted"].format(minutes=config.warn.mute_minutes) in entries[-1].text


async def test_entry_goes_out_even_if_the_chat_reply_fails(tg, conn, monkeypatch, chat, warn_topic):
    async def no_rights(message, text):
        raise TelegramBadRequest(SendMessage(chat_id=CHAT, text=text), "not enough rights to send text messages")

    monkeypatch.setattr(warn_handlers, "_reply", no_rights)
    with suppress(TelegramBadRequest):
        await tg.send(member(MOD), f"/warn @user{TARGET} флуд")

    # Предупреждение выдано (сбой Telegram транзакцию не откатывает) — значит, и запись о нём есть
    assert await warnings(conn) == ["флуд"]
    assert len(in_tracker(tg)) == 1


async def test_forged_button_data_leaves_other_messages_alone(tg, conn, texts, chat, warn_topic):
    await tg.send(member(MOD), f"/warn @user{TARGET} флуд")
    entry = in_tracker(tg)[0]
    lobby = next(request for request in tg.requests(SendMessage) if request.chat_id == CHAT)

    # Данные кнопки подделали и прислали к другому сообщению бота (будто к лобби игры): кнопки
    # с него не убираются. Модератор так всё равно снимает предупреждение — права у него есть
    await tg.click(member(MEMBER), tg.result(lobby).message_id, "warn:rm:999999")
    await tg.click(member(MOD), tg.result(lobby).message_id, remove_button(entry))

    assert tg.answers() == [texts._t["warn_remove_gone"], texts._t["warn_removed_answer"]]
    assert await warnings(conn) == []
    assert tg.requests(EditMessageReplyMarkup) == [] and tg.requests(EditMessageText) == []
