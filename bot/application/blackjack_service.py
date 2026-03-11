"""Сервис блекджека: колода, подсчёт очков, состояние раунда."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum


SUITS = ("♠️", "♥️", "♦️", "♣️")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
RANK_VALUES: dict[str, int] = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
    "J": 10, "Q": 10, "K": 10, "A": 11,
}


@dataclass(slots=True)
class Card:
    rank: str
    suit: str

    @property
    def value(self) -> int:
        return RANK_VALUES[self.rank]

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"


class GameResult(str, Enum):
    PLAYER_BLACKJACK = "player_blackjack"
    PLAYER_WIN = "player_win"
    DEALER_BUST = "dealer_bust"
    DEALER_WIN = "dealer_win"
    PLAYER_BUST = "player_bust"
    PUSH = "push"


@dataclass
class BlackjackRound:
    player_id: int
    chat_id: int
    bet: int
    deck: list[Card] = field(default_factory=list)
    player_hand: list[Card] = field(default_factory=list)
    dealer_hand: list[Card] = field(default_factory=list)
    finished: bool = False
    result: GameResult | None = None

    def _build_deck(self) -> list[Card]:
        cards = [Card(rank=r, suit=s) for s in SUITS for r in RANKS]
        random.shuffle(cards)
        return cards

    def deal(self) -> None:
        """Раздать начальные карты."""
        self.deck = self._build_deck()
        self.player_hand = [self.deck.pop(), self.deck.pop()]
        self.dealer_hand = [self.deck.pop(), self.deck.pop()]

        # Натуральный блекджек у игрока
        if hand_score(self.player_hand) == 21:
            self._finish_round()

    def hit(self) -> None:
        """Игрок берёт карту."""
        self.player_hand.append(self.deck.pop())
        if hand_score(self.player_hand) >= 21:
            self._finish_round()

    def stand(self) -> None:
        """Игрок останавливается, дилер доигрывает."""
        self._finish_round()

    def _finish_round(self) -> None:
        player_total = hand_score(self.player_hand)

        if player_total > 21:
            self.result = GameResult.PLAYER_BUST
            self.finished = True
            return

        # Натуральный блекджек (2 карты, 21)
        is_natural = len(self.player_hand) == 2 and player_total == 21

        # Дилер добирает до 17+
        while hand_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())

        dealer_total = hand_score(self.dealer_hand)

        if is_natural and dealer_total != 21:
            self.result = GameResult.PLAYER_BLACKJACK
        elif dealer_total > 21:
            self.result = GameResult.DEALER_BUST
        elif player_total > dealer_total:
            self.result = GameResult.PLAYER_WIN
        elif player_total < dealer_total:
            self.result = GameResult.DEALER_WIN
        else:
            self.result = GameResult.PUSH

        self.finished = True

    def payout_delta(self) -> int:
        """Возвращает изменение баланса (без учёта ставки, только выигрыш/проигрыш).

        PLAYER_BLACKJACK: +1.5x ставки (округлённо)
        PLAYER_WIN / DEALER_BUST: +1x ставки
        PUSH: 0
        PLAYER_BUST / DEALER_WIN: -1x ставки
        """
        if self.result == GameResult.PLAYER_BLACKJACK:
            return int(self.bet * 1.5)
        if self.result in (GameResult.PLAYER_WIN, GameResult.DEALER_BUST):
            return self.bet
        if self.result == GameResult.PUSH:
            return 0
        # bust / dealer win
        return -self.bet


def hand_score(hand: list[Card]) -> int:
    """Считает очки руки. Тузы автоматически считаются за 1, если перебор."""
    total = sum(c.value for c in hand)
    aces = sum(1 for c in hand if c.rank == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def format_hand(hand: list[Card], *, hide_second: bool = False) -> str:
    """Строка карт для отображения.

    hide_second=True — скрыть вторую карту дилера (показать рубашку).
    """
    if hide_second and len(hand) >= 2:
        return f"{hand[0]}  🂠"
    return "  ".join(str(c) for c in hand)
