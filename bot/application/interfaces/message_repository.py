from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from bot.domain.entities import User


@dataclass(slots=True)
class MessageInfo:
    message_id: int
    chat_id: int
    user_id: int
    sent_at: datetime
    text: str | None = None  # None = не сохраняем (команды, игровые ответы)
    is_reply: bool = False


@dataclass(slots=True)
class ChatMessage:
    """Сообщение с текстом и именем автора — для передачи в LLM."""

    message_id: int
    user_id: int
    username: str | None
    full_name: str
    text: str
    sent_at: datetime


class IMessageRepository(ABC):
    @abstractmethod
    async def save(self, info: MessageInfo) -> None: ...

    @abstractmethod
    async def delete_before(self, cutoff: datetime) -> int:
        """Удалить сообщения, отправленные раньше cutoff; вернуть, сколько удалено."""
        ...

    @abstractmethod
    async def get(self, chat_id: int, message_id: int) -> MessageInfo | None: ...

    @abstractmethod
    async def get_recent_with_text(
        self,
        chat_id: int,
        limit: int,
        user_ids: list[int] | None = None,
        since: datetime | None = None,
    ) -> list[ChatMessage]:
        """Вернуть до ``limit`` последних сообщений с текстом.

        Если ``user_ids`` указан — только от этих пользователей.
        Если ``since`` указан — только сообщения новее этого момента.
        Результат в хронологическом порядке (старые → новые).
        """
        ...

    @abstractmethod
    async def random_quote(self, chat_id: int, min_length: int, max_length: int, since: datetime) -> ChatMessage | None:
        """Случайное сообщение не бота длиной min_length..max_length, отправленное после since."""
        ...

    @abstractmethod
    async def random_authors(self, chat_id: int, exclude_user_id: int, since: datetime, limit: int) -> list[User]:
        """До limit случайных авторов (не ботов), писавших в чат после since, кроме exclude_user_id."""
        ...

    @abstractmethod
    async def get_active_chats(self) -> list[int]:
        """Вернуть все chat_id в которых есть хотя бы одно сообщение с текстом."""
        ...
