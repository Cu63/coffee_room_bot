"""Telegram без сети для e2e-тестов.

Апдейты проходят через настоящий Dispatcher со всеми middleware и роутерами (как в
bot/main.py), а запросы бота к Bot API попадают в FakeTelegramSession: она отвечает
правдоподобными объектами и записывает всё, что бот отправил.
"""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import AsyncGenerator
from typing import Any, TypeVar

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetChatMember,
    GetMe,
    PinChatMessage,
    PromoteChatMember,
    RestrictChatMember,
    SendMessage,
    SetMessageReaction,
    TelegramMethod,
    UnpinChatMessage,
)
from aiogram.types import CallbackQuery, Message, Update, User

from tests.integration.helpers import BOT_ID, CHAT

M = TypeVar("M", bound=TelegramMethod[Any])

BOT_USER = User(id=BOT_ID, is_bot=True, first_name="Кофебот", username="coffee_test_bot")
CHAT_JSON = {"id": CHAT, "type": "supergroup", "title": "Кофейня"}


def member(uid: int, **fields: Any) -> User:
    """Участник чата; seed_users заводит его в БД с тем же username."""
    return User(**{"id": uid, "is_bot": False, "first_name": f"User {uid}", "username": f"user{uid}", **fields})


def _message_json(message_id: int, text: str, sender: User = BOT_USER) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "date": int(time.time()),
        "chat": CHAT_JSON,
        "from": sender.model_dump(mode="json", exclude_none=True),
        "text": text,
    }


class FakeTelegramSession(BaseSession):
    """Bot API без сети: отвечает как Telegram и запоминает каждый запрос с ответом."""

    def __init__(self) -> None:
        super().__init__()
        self.log: list[tuple[TelegramMethod[Any], Any]] = []
        self._message_ids = itertools.count(10_000)
        # Статус в чате для getChatMember: member, left, kicked, creator. По умолчанию — member.
        self.member_status: dict[int, str] = {}

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        content = json.dumps({"ok": True, "result": self._result(method)})
        result = self.check_response(bot=bot, method=method, status_code=200, content=content).result
        self.log.append((method, result))
        return result

    def _result(self, method: TelegramMethod[Any]) -> Any:
        match method:
            case SendMessage():
                return _message_json(next(self._message_ids), method.text)
            case EditMessageText():
                return _message_json(method.message_id, method.text)
            case GetMe():
                return BOT_USER.model_dump(mode="json", exclude_none=True)
            case GetChatMember():
                return self._chat_member_json(method.user_id)
            case (
                AnswerCallbackQuery()
                | PinChatMessage()
                | UnpinChatMessage()
                | DeleteMessage()
                | SetMessageReaction()
                | RestrictChatMember()
                | PromoteChatMember()
            ):
                return True
        raise NotImplementedError(f"фейковый Telegram не умеет {type(method).__name__}")

    def _chat_member_json(self, user_id: int) -> dict[str, Any]:
        status = self.member_status.get(user_id, "member")
        result: dict[str, Any] = {"status": status, "user": member(user_id).model_dump(mode="json", exclude_none=True)}
        if status == "kicked":
            result["until_date"] = 0
        if status == "creator":
            result["is_anonymous"] = False
        return result

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError
        yield b""

    async def close(self) -> None:
        pass


class TelegramHarness:
    """Пишет в чат от имени пользователей и показывает, что ответил бот."""

    def __init__(self, dp: Dispatcher, bot: Bot, session: FakeTelegramSession) -> None:
        self.dp = dp
        self.bot = bot
        self.session = session
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(1)

    async def send(self, user: User, text: str) -> int:
        """Сообщение пользователя в чат; возвращает его message_id."""
        message_id = next(self._message_ids)
        message = Message.model_validate(_message_json(message_id, text, sender=user))
        await self.dp.feed_update(self.bot, Update(update_id=next(self._update_ids), message=message))
        return message_id

    async def click(self, user: User, message_id: int, data: str) -> None:
        """Нажатие инлайн-кнопки под сообщением бота."""
        update_id = next(self._update_ids)
        callback = CallbackQuery(
            id=str(update_id),
            from_user=user,
            chat_instance="test",
            message=Message.model_validate(_message_json(message_id, "…")),
            data=data,
        )
        await self.dp.feed_update(self.bot, Update(update_id=update_id, callback_query=callback))

    def requests(self, method_type: type[M]) -> list[M]:
        return [method for method, _ in self.session.log if isinstance(method, method_type)]

    def result(self, request: TelegramMethod[Any]) -> Any:
        """Что Telegram ответил на запрос (например, отправленное Message с его message_id)."""
        return next(result for method, result in self.session.log if method is request)

    def answers(self) -> list[str | None]:
        """Тексты ответов на нажатия кнопок."""
        return [answer.text for answer in self.requests(AnswerCallbackQuery)]
