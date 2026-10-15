from abc import ABC, abstractmethod

from bot.domain.entities import Score


class IScoreRepository(ABC):
    @abstractmethod
    async def get(self, user_id: int, chat_id: int) -> Score | None: ...

    @abstractmethod
    async def add_delta(self, user_id: int, chat_id: int, delta: int) -> int:
        """Атомарно изменяет счёт на delta. Возвращает новое значение."""
        ...

    @abstractmethod
    async def try_debit(self, user_id: int, chat_id: int, amount: int) -> int | None:
        """Атомарно списывает amount, только если баланс >= amount.

        Возвращает новый баланс или None, если баллов не хватило.
        Проверка и списание — один запрос, поэтому параллельные траты
        не уводят баланс в минус. amount=0 всегда успешно, amount<0 — ValueError.
        """
        ...

    @abstractmethod
    async def debit_up_to(self, user_id: int, chat_id: int, amount: int) -> int:
        """Атомарно списывает min(amount, баланс), не уводя баланс в минус.

        Возвращает фактически списанную сумму (0, если списывать нечего).
        amount<0 — ValueError.
        """
        ...

    @abstractmethod
    async def set_value(self, user_id: int, chat_id: int, value: int) -> int:
        """Устанавливает счёт в конкретное значение. Возвращает установленное значение."""
        ...

    @abstractmethod
    async def top(self, chat_id: int, limit: int) -> list[Score]: ...

    @abstractmethod
    async def bottom(self, chat_id: int, limit: int) -> list[Score]:
        """Пользователи с наименьшим счётом (антирейтинг), ascending."""
        ...

    @abstractmethod
    async def get_rank(self, user_id: int, chat_id: int) -> int | None:
        """Место пользователя в рейтинге чата (1 = первое). None если нет счёта."""
        ...

    @abstractmethod
    async def get_all_user_ids(self, chat_id: int) -> list[int]:
        """Все user_id с ненулевым счётом в чате (без ботов)."""
        ...

    @abstractmethod
    async def get_rich_users(self, chat_id: int, min_balance: int) -> list[Score]:
        """Все пользователи чата с балансом >= min_balance (без ботов)."""
        ...

    @abstractmethod
    async def get_random_user(self, chat_id: int, exclude_id: int) -> tuple[int, str | None, str] | None:
        """Случайный пользователь из чата (без ботов, исключая exclude_id).

        Возвращает (user_id, username, full_name) или None.
        """
        ...