from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MessageInfo:
    message_id: int
    chat_id: int
    user_id: int
    sent_at: datetime
    text: str | None = None  # None = не сохраняем (команды, игровые ответы)


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
    async def get(self, chat_id: int, message_id: int) -> MessageInfo | None: ...

    @abstractmethod
    async def get_recent_with_text(
        self,
        chat_id: int,
        limit: int,
        user_ids: list[int] | None = None,
    ) -> list[ChatMessage]:
        """Вернуть до ``limit`` последних сообщений с текстом.

        Если ``user_ids`` указан — только от этих пользователей.
        Результат в хронологическом порядке (старые → новые).
        """
        ...
