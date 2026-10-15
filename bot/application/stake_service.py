"""Ставки пошаговых игр (BJ, TTT, дуэли): деньги лежат в банке игры до расчёта.

Ставка списывается с игрока и кладётся в банк (game_pots), выигрыш и возвраты
берутся из банка. Из банка нельзя взять больше, чем в него положили: если игру
по ошибке рассчитают второй раз, выплата упадёт с PotOverdraft и транзакция
откатится, а не начислит деньги из воздуха.

Игры, начатые до появления банков, своего банка не имеют: для них pot=False,
и деньги идут мимо банка, как раньше.
"""

from __future__ import annotations

import logging

from bot.application.interfaces.game_pot_repository import IGamePotRepository, PotTake
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.score_service import SpendResult
from bot.domain.ledger import LedgerKind

logger = logging.getLogger(__name__)


class PotOverdraft(Exception):
    """Из банка игры хотят взять больше, чем в нём есть: игру рассчитывают второй раз."""

    def __init__(self, chat_id: int, ref: str, amount: int, result: PotTake) -> None:
        super().__init__(f"банк {ref} в чате {chat_id}: нельзя взять {amount} ({result})")


class StakeService:
    def __init__(self, score_repo: IScoreRepository, pot_repo: IGamePotRepository) -> None:
        self._scores = score_repo
        self._pots = pot_repo

    async def hold(self, user_id: int, chat_id: int, ref: str, amount: int, *, pot: bool = True) -> SpendResult:
        """Списать ставку и положить её в банк игры. Не хватает баллов — отказ, ничего не списано."""
        new_balance = await self._scores.try_debit(user_id, chat_id, amount, kind=LedgerKind.STAKE, ref=ref)
        if new_balance is None:
            score = await self._scores.get(user_id, chat_id)
            return SpendResult(success=False, cost=amount, current_balance=score.value if score else 0)
        if pot:
            await self._pots.put(chat_id, ref, amount)
        return SpendResult(success=True, cost=amount, new_balance=new_balance)

    async def pay(
        self, user_id: int, chat_id: int, ref: str, amount: int, *, kind: LedgerKind, pot: bool = True
    ) -> int:
        """Выплатить из банка игры (PRIZE — выигрыш, REFUND — возврат). Возвращает новый баланс."""
        if pot:
            result = await self._pots.take(chat_id, ref, amount)
            if result is not PotTake.TAKEN:
                raise PotOverdraft(chat_id, ref, amount, result)
        return await self._scores.add_delta(user_id, chat_id, amount, kind=kind, ref=ref)
