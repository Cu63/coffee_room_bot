from datetime import datetime
from bot.domain.tz import TZ_MSK

from bot.domain.entities import MuteEntry
from bot.application.interfaces.mute_repository import IMuteRepository


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