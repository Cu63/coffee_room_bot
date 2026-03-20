from datetime import datetime, timedelta

from bot.application.interfaces.mute_repository import IMuteRepository
from bot.domain.entities import MuteEntry
from bot.domain.tz import TZ_MSK


class MuteService:
    def __init__(self, mute_repo: IMuteRepository) -> None:
        self._repo = mute_repo

    async def save_mute(self, entry: MuteEntry) -> None:
        await self._repo.save(entry)

    async def get_mute(self, user_id: int, chat_id: int) -> MuteEntry | None:
        return await self._repo.get(user_id, chat_id)

    async def delete_mute(self, user_id: int, chat_id: int) -> None:
        await self._repo.delete(user_id, chat_id)

    async def get_expired_mutes(self) -> list[MuteEntry]:
        return await self._repo.get_expired(datetime.now(TZ_MSK))

    async def log_mute(self, user_id: int, muted_by: int, chat_id: int) -> None:
        await self._repo.log_mute(user_id, muted_by, chat_id)

    async def compute_stacked_until(
        self,
        user_id: int,
        chat_id: int,
        add_seconds: int,
    ) -> tuple[datetime, bool]:
        """Вычисляет новый until_at с учётом стекования.

        Если на пользователя уже наложен активный мут — новая длительность
        прибавляется к оставшемуся времени, а не заменяет его.

        Возвращает (new_until, was_stacked):
          was_stacked=True  — существовал активный мут, время накоплено
          was_stacked=False — мута не было, отсчёт идёт от текущего момента
        """
        now = datetime.now(TZ_MSK)
        existing = await self._repo.get(user_id, chat_id)
        if existing is not None and existing.until_at > now:
            return existing.until_at + timedelta(seconds=add_seconds), True
        return now + timedelta(seconds=add_seconds), False
