from abc import ABC, abstractmethod


class ILlmRepository(ABC):
    @abstractmethod
    async def count_today(self, user_id: int) -> int:
        """Количество запросов пользователя за сегодня."""
        ...

    @abstractmethod
    async def log_request(
        self,
        user_id: int,
        chat_id: int,
        command: str,
        query: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Записывает запрос в лог."""
        ...
