from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

from bot.application.interfaces.giveaway_repository import IGiveawayRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.domain.giveaway_entities import Giveaway, GiveawayStatus, GiveawayWinner


@dataclass
class FinishResult:
    giveaway: Giveaway
    winners: list[tuple[int, int]]  # [(user_id, prize), ...]
    participants_count: int


class GiveawayService:
    def __init__(
        self,
        giveaway_repo: IGiveawayRepository,
        score_repo: IScoreRepository,
    ) -> None:
        self._giveaway_repo = giveaway_repo
        self._score_repo = score_repo

    async def create(
        self,
        chat_id: int,
        created_by: int,
        prizes: list[int],
        ends_at: datetime | None = None,
    ) -> Giveaway:
        giveaway = Giveaway(
            chat_id=chat_id,
            created_by=created_by,
            prizes=prizes,
            ends_at=ends_at,
        )
        return await self._giveaway_repo.create(giveaway)

    async def set_message_id(self, giveaway_id: int, message_id: int) -> None:
        await self._giveaway_repo.update_message_id(giveaway_id, message_id)

    async def join(self, giveaway_id: int, user_id: int) -> bool:
        """Записать участника. Возвращает True если успешно, False если уже участвует."""
        giveaway = await self._giveaway_repo.get(giveaway_id)
        if giveaway is None or giveaway.status != GiveawayStatus.ACTIVE:
            return False
        return await self._giveaway_repo.add_participant(giveaway_id, user_id)

    async def count_participants(self, giveaway_id: int) -> int:
        return await self._giveaway_repo.count_participants(giveaway_id)

    async def get_active_in_chat(self, chat_id: int) -> list[Giveaway]:
        return await self._giveaway_repo.get_active_in_chat(chat_id)

    async def get(self, giveaway_id: int) -> Giveaway | None:
        return await self._giveaway_repo.get(giveaway_id)

    async def finish(self, giveaway_id: int) -> FinishResult | None:
        """Завершить розыгрыш: выбрать победителей, начислить баллы.

        Возвращает None если розыгрыш не найден или уже завершён.
        """
        giveaway = await self._giveaway_repo.get(giveaway_id)
        if giveaway is None or giveaway.status != GiveawayStatus.ACTIVE:
            return None

        participants = await self._giveaway_repo.get_participants(giveaway_id)
        participants_count = len(participants)

        # Перемешиваем и берём столько победителей, сколько есть участников
        shuffled = participants.copy()
        random.shuffle(shuffled)
        winners_count = min(len(giveaway.prizes), len(shuffled))
        winner_ids = shuffled[:winners_count]

        winner_entities: list[GiveawayWinner] = []
        winners_with_prizes: list[tuple[int, int]] = []

        for position, (user_id, prize) in enumerate(
            zip(winner_ids, giveaway.prizes), start=1
        ):
            winner_entities.append(
                GiveawayWinner(
                    giveaway_id=giveaway_id,
                    user_id=user_id,
                    prize=prize,
                    position=position,
                )
            )
            winners_with_prizes.append((user_id, prize))

        # Начисляем баллы победителям
        for user_id, prize in winners_with_prizes:
            await self._score_repo.add_delta(user_id, giveaway.chat_id, prize)

        await self._giveaway_repo.save_winners(winner_entities)
        await self._giveaway_repo.finish(giveaway_id)

        return FinishResult(
            giveaway=giveaway,
            winners=winners_with_prizes,
            participants_count=participants_count,
        )

    async def finish_expired(self, now: datetime) -> list[FinishResult]:
        """Завершить все просроченные розыгрыши. Вызывается из фоновой задачи."""
        expired = await self._giveaway_repo.get_expired(now)
        results = []
        for giveaway in expired:
            result = await self.finish(giveaway.id)  # type: ignore[arg-type]
            if result is not None:
                results.append(result)
        return results