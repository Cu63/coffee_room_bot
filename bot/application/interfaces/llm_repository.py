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

    @abstractmethod
    async def sum_input_tokens_today(
        self,
        user_id: int,
        commands: tuple[str, ...],
    ) -> int:
        """Сумма input-токенов пользователя за сегодня по указанным командам."""
        ...

    @abstractmethod
    async def sum_tokens_global_today(
        self,
        commands: tuple[str, ...],
    ) -> tuple[int, int]:
        """Глобальная сумма (input, output) по всем пользователям за сегодня."""
        ...
