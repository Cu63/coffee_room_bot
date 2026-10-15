import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import EditMessageText, GetUpdates, SendDice, SendMessage

import bot.infrastructure.telegram_retry as telegram_retry
from bot.infrastructure.telegram_retry import RetryIdempotentRequestsMiddleware


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def _sleep(_):
        return None

    monkeypatch.setattr(telegram_retry.asyncio, "sleep", _sleep)


def flaky(failures: int):
    calls = []

    async def make_request(bot, method):
        calls.append(method)
        if len(calls) <= failures:
            raise TelegramNetworkError(method=method, message="Request timeout error")
        return "ok"

    return make_request, calls


async def test_idempotent_method_is_retried():
    make_request, calls = flaky(failures=2)
    method = EditMessageText(chat_id=1, message_id=2, text="x")
    assert await RetryIdempotentRequestsMiddleware()(make_request, None, method) == "ok"
    assert len(calls) == 3


async def test_idempotent_method_gives_up_after_three_attempts():
    make_request, calls = flaky(failures=5)
    method = EditMessageText(chat_id=1, message_id=2, text="x")
    with pytest.raises(TelegramNetworkError):
        await RetryIdempotentRequestsMiddleware()(make_request, None, method)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "method",
    [SendMessage(chat_id=1, text="x"), SendDice(chat_id=1), GetUpdates()],
)
async def test_non_idempotent_method_is_not_retried(method):
    make_request, calls = flaky(failures=1)
    with pytest.raises(TelegramNetworkError):
        await RetryIdempotentRequestsMiddleware()(make_request, None, method)
    assert len(calls) == 1
