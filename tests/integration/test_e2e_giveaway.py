"""E2E розыгрыша и мут-рулетки: запуск, вступление кнопкой, итог. Тексты — из messages.yaml."""

from __future__ import annotations

import pytest
from aiogram.methods import EditMessageReplyMarkup, RestrictChatMember, SendMessage

from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from tests.integration.fake_telegram import member
from tests.integration.helpers import balance, seed_users

ADMIN, ANNA = 901, 902


@pytest.fixture
async def texts(app_container) -> MessageFormatter:
    return await app_container.get(MessageFormatter)


@pytest.fixture
async def admin(app_container, conn) -> None:
    await seed_users(conn, ADMIN, ANNA)
    (await app_container.get(AppConfig)).admin.users = [str(ADMIN)]


def posted(tg) -> tuple[int, str, str]:
    """Последний пост бота с кнопкой: id сообщения, текст, callback_data кнопки."""
    post = next(request for request in reversed(tg.requests(SendMessage)) if request.reply_markup is not None)
    [[button]] = post.reply_markup.inline_keyboard
    return tg.result(post).message_id, post.text, button.callback_data


def last_button_text(tg) -> str:
    [[button]] = tg.requests(EditMessageReplyMarkup)[-1].reply_markup.inline_keyboard
    return button.text


async def test_giveaway_start_join_and_results(tg, conn, texts, admin):
    t = texts._t
    # Голое число в конце — приз, а не 50 минут: раньше третий приз из подсказки терялся
    await tg.send(member(ADMIN), "/giveaway 100 50")

    message_id, text, join = posted(tg)
    assert text.startswith("🎉 <b>Розыгрыш запущен!</b>") and "🥇 100 " in text and "🥈 50 " in text
    assert f"<b>{t['giveaway_manual']}</b>" in text

    await tg.click(member(ANNA), message_id, join)
    await tg.click(member(ANNA), message_id, join)
    assert tg.answers() == [t["giveaway_joined"], t["giveaway_already_joined"]]
    assert last_button_text(tg) == t["giveaway_join_button"].format(count=1)

    await tg.send(member(ADMIN), "/giveaway_end")

    result = tg.requests(SendMessage)[-1].text
    assert result.startswith(t["giveaway_results"].format(count=1))
    assert f'🥇 <a href="tg://user?id={ANNA}">User {ANNA}</a> — +100 ' in result
    assert await balance(conn, ANNA) == 100


async def test_giveaway_refuses_bad_prize_with_escaping(tg, texts, admin):
    await tg.send(member(ADMIN), "/giveaway <b>")
    assert tg.requests(SendMessage)[-1].text == texts._t["giveaway_bad_prize"].format(token="&lt;b&gt;")


async def test_mute_roulette_start_join_and_results(tg, texts, admin):
    t = texts._t
    await tg.send(member(ADMIN), "/mutegiveaway 10m 1 1m")

    message_id, text, join = posted(tg)
    assert text.startswith("🎰 <b>Мут-гивэвей!</b>") and "Сбор: <b>1м</b>" in text

    await tg.click(member(ANNA), message_id, join)
    assert tg.answers() == [t["mute_roulette_joined"]]
    assert last_button_text(tg) == t["mute_roulette_button"].format(count=1)

    await tg.send(member(ADMIN), "/mutegiveaway_end")

    result = tg.requests(SendMessage)[-1].text
    assert result.startswith(t["mute_roulette_results"].format(count=1))
    name = f'<a href="tg://user?id={ANNA}">User {ANNA}</a>'
    assert t["mute_roulette_muted"].format(name=name, minutes=10) in result
    assert [request.user_id for request in tg.requests(RestrictChatMember)] == [ANNA]
