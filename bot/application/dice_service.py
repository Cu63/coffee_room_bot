from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from bot.application.interfaces.dice_repository import IDiceRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.domain.dice_entities import DiceGame, DiceGameStatus


@dataclass
class CreateResult:
    game: DiceGame | None
    not_enough: bool = False
    user_already_in_game: bool = False  # создатель уже участвует в активной игре чата


@dataclass
class JoinResult:
    success: bool
    already_joined: bool = False        # уже в этой игре
    already_in_other_game: bool = False  # уже в другой активной игре чата
    not_enough: bool = False
    game_not_found: bool = False
    balance: int = 0
    bet: int = 0


@dataclass
class FinishResult:
    game: DiceGame
    participants: list[int]
    dice_results: dict[int, int]         # user_id -> dice_value
    winners: list[int]                   # user_ids с максимальным значением
    prize_per_winner: int
    total_pot: int


class DiceService:
    def __init__(
        self,
        dice_repo: IDiceRepository,
        score_repo: IScoreRepository,
        stats_repo: IUserStatsRepository,
    ) -> None:
        self._dice_repo = dice_repo
        self._score_repo = score_repo
        self._stats_repo = stats_repo

    async def create(
        self,
        chat_id: int,
        created_by: int,
        bet: int,
        ends_at: datetime,
    ) -> CreateResult:
        """Создаёт игру и списывает ставку с создателя."""
        # Игрок не может участвовать более чем в одной активной игре
        if await self._dice_repo.is_user_in_active_game(chat_id, created_by):
            return CreateResult(game=None, user_already_in_game=True)

        score = await self._score_repo.get(created_by, chat_id)
        balance = score.value if score else 0
        if balance < bet:
            return CreateResult(game=None, not_enough=True)

        game = DiceGame(chat_id=chat_id, bet=bet, ends_at=ends_at, created_by=created_by)
        game = await self._dice_repo.create(game)
        await self._score_repo.add_delta(created_by, chat_id, -bet)
        await self._dice_repo.add_participant(game.id, created_by)  # type: ignore[arg-type]
        return CreateResult(game=game)

    async def set_message_id(self, game_id: int, message_id: int) -> None:
        await self._dice_repo.update_message_id(game_id, message_id)

    async def join(self, game_id: int, user_id: int) -> JoinResult:
        """Участник присоединяется к игре. Списывает ставку."""
        game = await self._dice_repo.get(game_id)
        if game is None or game.status != DiceGameStatus.PENDING:
            return JoinResult(success=False, game_not_found=True)

        participants = await self._dice_repo.get_participants(game_id)
        if user_id in participants:
            return JoinResult(success=False, already_joined=True)

        # Проверяем, не участвует ли уже в другой игре этого чата
        if await self._dice_repo.is_user_in_active_game(game.chat_id, user_id):
            return JoinResult(success=False, already_in_other_game=True)

        score = await self._score_repo.get(user_id, game.chat_id)
        balance = score.value if score else 0
        if balance < game.bet:
            return JoinResult(success=False, not_enough=True, balance=balance, bet=game.bet)

        await self._dice_repo.add_participant(game_id, user_id)
        await self._score_repo.add_delta(user_id, game.chat_id, -game.bet)
        return JoinResult(success=True)

    async def count_participants(self, game_id: int) -> int:
        return await self._dice_repo.count_participants(game_id)

    async def finish(self, game_id: int, dice_results: dict[int, int]) -> FinishResult | None:
        """Завершает игру с уже известными значениями костей. Распределяет призы."""
        game = await self._dice_repo.get(game_id)
        if game is None or game.status != DiceGameStatus.PENDING:
            return None

        participants = await self._dice_repo.get_participants(game_id)
        total_pot = game.bet * len(participants)

        if not participants or not dice_results:
            # Нет участников — возврат ставок (не должно случаться)
            for uid in participants:
                await self._score_repo.add_delta(uid, game.chat_id, game.bet)
            await self._dice_repo.finish(game_id)
            return FinishResult(
                game=game,
                participants=participants,
                dice_results={},
                winners=participants,
                prize_per_winner=game.bet,
                total_pot=total_pot,
            )

        max_value = max(dice_results.values())
        winners = [uid for uid, val in dice_results.items() if val == max_value]
        prize_per_winner = total_pot // len(winners)
        remainder = total_pot - prize_per_winner * len(winners)

        for winner_id in winners:
            await self._score_repo.add_delta(winner_id, game.chat_id, prize_per_winner)
        # Остаток (при нечётном делении) — первому победителю
        if remainder > 0:
            await self._score_repo.add_delta(winners[0], game.chat_id, remainder)

        # Победа засчитывается только в многопользовательской игре
        if len(participants) > 1:
            for winner_id in winners:
                await self._stats_repo.add_win(winner_id, game.chat_id, "dice")

        await self._dice_repo.finish(game_id)
        return FinishResult(
            game=game,
            participants=participants,
            dice_results=dice_results,
            winners=winners,
            prize_per_winner=prize_per_winner,
            total_pot=total_pot,
        )

    async def get_participants(self, game_id: int) -> list[int]:
        return await self._dice_repo.get_participants(game_id)

    async def get_expired(self, now: datetime) -> list[DiceGame]:
        """Возвращает игры, у которых истёкло время сбора участников."""
        return await self._dice_repo.get_expired(now)
