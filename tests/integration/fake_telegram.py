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
    GetChat,
    GetChatAdministrators,
    GetChatMember,
    GetMe,
    PinChatMessage,
    PromoteChatMember,
    RestrictChatMember,
    SendMessage,
    SetChatAdministratorCustomTitle,
    SetChatPermissions,
    SetMessageReaction,
    TelegramMethod,
    UnpinChatMessage,
)
from aiogram.types import CallbackQuery, Message, Update, User

from tests.integration.helpers import BOT_ID, CHAT

M = TypeVar("M", bound=TelegramMethod[Any])

# Права, с которыми getChatMember отдаёт статус administrator
ADMIN_RIGHTS: dict[str, Any] = {
    "can_be_edited": True,
    "is_anonymous": False,
    "can_manage_chat": True,
    "can_delete_messages": True,
    "can_manage_video_chats": False,
    "can_restrict_members": False,
    "can_promote_members": False,
    "can_change_info": False,
    "can_invite_users": True,
    "can_post_stories": False,
    "can_edit_stories": False,
    "can_delete_stories": False,
    "can_pin_messages": True,
    "custom_title": "модер",
}
# Права, которые выдаёт promoteChatMember (все, кроме can_be_edited и титула)
_PROMOTE_RIGHTS = tuple(name for name in ADMIN_RIGHTS if name not in ("can_be_edited", "custom_title"))
_RESTRICTED_FLAGS = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_edit_tag",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
)

BOT_USER = User(id=BOT_ID, is_bot=True, first_name="Кофебот", username="coffee_test_bot")
CHAT_JSON = {"id": CHAT, "type": "supergroup", "title": "Кофейня"}
# Права участников чата по умолчанию (getChat → permissions): писать можно всё, менять чат и закреплять — нет
CHAT_PERMISSIONS: dict[str, bool] = {
    "can_send_messages": True,
    "can_send_audios": True,
    "can_send_documents": True,
    "can_send_photos": True,
    "can_send_videos": True,
    "can_send_video_notes": True,
    "can_send_voice_notes": True,
    "can_send_polls": True,
    "can_send_other_messages": True,
    "can_add_web_page_previews": True,
    "can_change_info": False,
    "can_invite_users": True,
    "can_pin_messages": False,
}


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
        # Статус в чате для getChatMember: member, administrator, restricted, left, kicked, creator.
        # По умолчанию — member. promoteChatMember меняет его так же, как Telegram.
        self.member_status: dict[int, str] = {}
        # Права админов, назначенных через promoteChatMember; остальные администраторы — с ADMIN_RIGHTS
        self.admin_rights: dict[int, dict[str, Any]] = {}
        # Права участников чата: getChat отдаёт, setChatPermissions меняет
        self.chat_permissions: dict[str, bool] = dict(CHAT_PERMISSIONS)

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
            case GetChatAdministrators():
                admins = [uid for uid, status in self.member_status.items() if status in ("administrator", "creator")]
                return [self._chat_member_json(uid) for uid in admins]
            case GetChat():
                return CHAT_JSON | {
                    "accent_color_id": 0,
                    "max_reaction_count": 11,
                    "accepted_gift_types": {
                        "unlimited_gifts": True,
                        "limited_gifts": True,
                        "unique_gifts": True,
                        "premium_subscription": True,
                        "gifts_from_channels": True,
                    },
                    "permissions": self.chat_permissions,
                }
            case SetChatPermissions():
                self.chat_permissions = method.permissions.model_dump(mode="json", exclude_none=True)
                return True
            case PromoteChatMember():
                self._promote(method)
                return True
            case SetChatAdministratorCustomTitle():
                self.admin_rights.setdefault(method.user_id, dict(ADMIN_RIGHTS))["custom_title"] = method.custom_title
                return True
            case (
                AnswerCallbackQuery()
                | PinChatMessage()
                | UnpinChatMessage()
                | DeleteMessage()
                | SetMessageReaction()
                | RestrictChatMember()
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
        if status == "administrator":
            result |= self.admin_rights.get(user_id, ADMIN_RIGHTS)
        if status == "restricted":
            result |= {field: False for field in _RESTRICTED_FLAGS} | {"is_member": True, "until_date": 0}
        return result

    def _promote(self, method: PromoteChatMember) -> None:
        """Хоть одно право — администратор ровно с этими правами; ни одного — снова обычный участник."""
        rights = {name: bool(getattr(method, name)) for name in _PROMOTE_RIGHTS}
        if any(value for name, value in rights.items() if name.startswith("can_")):
            self.member_status[method.user_id] = "administrator"
            self.admin_rights[method.user_id] = rights | {"can_be_edited": True}
        elif self.member_status.get(method.user_id) == "administrator":
            self.member_status[method.user_id] = "member"
            self.admin_rights.pop(method.user_id, None)

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
