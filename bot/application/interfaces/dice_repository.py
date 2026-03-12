from abc import ABC, abstractmethod
from datetime import datetime

from bot.domain.dice_entities import DiceGame


class IDiceRepository(ABC):
    @abstractmethod
    async def create(self, game: DiceGame) -> DiceGame:
        """Создаёт игру, возвращает с заполненным id."""
        ...

    @abstractmethod
    async def update_message_id(self, game_id: int, message_id: int) -> None:
        """Сохраняет id сообщения-анонса."""
        ...

    @abstractmethod
    async def get(self, game_id: int) -> DiceGame | None: ...

    @abstractmethod
    async def get_pending_in_chat(self, chat_id: int) -> DiceGame | None:
        """Возвращает активную (pending) игру в чате, или None."""
        ...

    @abstractmethod
    async def finish(self, game_id: int) -> None:
        """Переводит статус в finished."""
        ...

    @abstractmethod
    async def get_expired(self, now: datetime) -> list[DiceGame]:
        """Возвращает игры со статусом pending и ends_at <= now."""
        ...

    @abstractmethod
    async def add_participant(self, game_id: int, user_id: int) -> bool:
        """Добавляет участника. Возвращает False если уже участвует."""
        ...

    @abstractmethod
    async def get_participants(self, game_id: int) -> list[int]:
        """Список user_id всех участников."""
        ...

    @abstractmethod
    async def count_participants(self, game_id: int) -> int: ...
