from datetime import datetime, timedelta

from bot.application.interfaces.mute_repository import IMuteRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.domain.entities import ActiveMute, MuteEntry, User
from bot.domain.ledger import LedgerKind
from bot.domain.tz import TZ_MSK


class MuteService:
    def __init__(self, mute_repo: IMuteRepository, score_repo: IScoreRepository) -> None:
        self._repo = mute_repo
        self._scores = score_repo

    async def save_mute(self, entry: MuteEntry) -> None:
        await self._repo.save(entry)

    async def get_mute(self, user_id: int, chat_id: int) -> MuteEntry | None:
        return await self._repo.get(user_id, chat_id)

    async def delete_mute(self, user_id: int, chat_id: int) -> int:
        """Удаляет мут; возвращает штраф за него — только тому, чьё удаление прошло."""
        return await self._repo.delete(user_id, chat_id)

    async def charge_fine(self, user_id: int, chat_id: int, amount: int, *, bot_id: int, ref: str) -> int:
        """Штраф за наложенный мут: до amount (в минус не уводит) в кассу бота и в запись мута.

        Звать после того, как мут наложен. Возвращает списанное.
        """
        if amount <= 0:
            return 0
        charged = await self._scores.debit_up_to(
            user_id, chat_id, amount, kind=LedgerKind.PENALTY, ref=ref, counterparty_id=bot_id
        )
        if charged:
            await self._scores.add_delta(
                bot_id, chat_id, charged, kind=LedgerKind.PENALTY, ref=ref, counterparty_id=user_id
            )
            await self._repo.add_fine(user_id, chat_id, charged)
        return charged

    async def refund_fine(self, user_id: int, chat_id: int, fine: int, *, bot_id: int) -> int:
        """Вернуть штраф снятого мута из кассы бота. Касса могла потратиться — вернёт, сколько в ней есть."""
        if fine <= 0:
            return 0
        refunded = await self._scores.debit_up_to(
            bot_id, chat_id, fine, kind=LedgerKind.REFUND, ref="unmute", counterparty_id=user_id
        )
        if refunded:
            await self._scores.add_delta(
                user_id, chat_id, refunded, kind=LedgerKind.REFUND, ref="unmute", counterparty_id=bot_id
            )
        return refunded

    async def get_expired_mutes(self) -> list[MuteEntry]:
        return await self._repo.get_expired(datetime.now(TZ_MSK))

    async def set_admin_title(self, user_id: int, chat_id: int, title: str | None) -> None:
        """Титул, который замученный админ получит обратно при размуте (None — без титула)."""
        await self._repo.set_admin_title(user_id, chat_id, title)

    async def active_in_chat(self, chat_id: int) -> list[ActiveMute]:
        """Действующие муты чата — сначала те, что кончатся раньше."""
        return await self._repo.active_in_chat(chat_id)

    async def released_users(self, chat_id: int) -> list[User]:
        """Все, кого бот когда-либо мутил в чате и кто сейчас не в муте."""
        return await self._repo.released_users(chat_id)

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
