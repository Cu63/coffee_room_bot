from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass(slots=True)
class DailyLeader:
    """Лидер в одной категории за день."""
    user_id: int
    username: str | None
    full_name: str
    value: int  # количество (сообщений / реакций / реплаев / побед)


@dataclass(slots=True)
class DailyLeaderboard:
    """Полный лидерборд за один день."""
    date: date
    top_messages: DailyLeader | None = None
    top_reactions_given: DailyLeader | None = None
    top_reactions_received: DailyLeader | None = None
    top_replies: DailyLeader | None = None
    top_ttt_wins: DailyLeader | None = None
    top_wordgame_wins: DailyLeader | None = None  # word + rword суммарно

    def is_empty(self) -> bool:
        return all(
            v is None for v in (
                self.top_messages,
                self.top_reactions_given,
                self.top_reactions_received,
                self.top_replies,
                self.top_ttt_wins,
                self.top_wordgame_wins,
            )
        )


class IDailyLeaderboardRepository(ABC):

    @abstractmethod
    async def get_daily_leaderboard(self, chat_id: int, for_date: date) -> DailyLeaderboard:
        """Собрать лидерборд за указанный день."""
        ...

    @abstractmethod
    async def add_game_win(
        self,
        user_id: int,
        chat_id: int,
        game: str,  # "ttt" | "word" | "rword"
        for_date: date,
    ) -> None:
        """Записать победу в игре за день."""
        ...

    @abstractmethod
    async def get_active_chats(self) -> list[int]:
        """Вернуть все chat_id где есть хоть какая-то активность (из messages)."""
        ...
