from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MessageInfo:
    message_id: int
    chat_id: int
    user_id: int
    sent_at: datetime


class IMessageRepository(ABC):
    @abstractmethod
    async def save(self, info: MessageInfo) -> None: ...

    @abstractmethod
    async def get(self, chat_id: int, message_id: int) -> MessageInfo | None: ...
