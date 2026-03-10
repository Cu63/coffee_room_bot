from bot.domain.entities import Score
from bot.application.interfaces.score_repository import IScoreRepository


class LeaderboardService:
    def __init__(self, score_repo: IScoreRepository) -> None:
        self._score_repo = score_repo

    async def get_top(self, chat_id: int, limit: int = 10) -> list[Score]:
        return await self._score_repo.top(chat_id, limit)
