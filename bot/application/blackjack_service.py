"""Утилиты блекджека: колода, подсчёт очков, форматирование карт."""

from __future__ import annotations

import random
from dataclasses import dataclass

SUITS = ("♠️", "♥️", "♦️", "♣️")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
RANK_VALUES: dict[str, int] = {
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
    "A": 11,
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


def build_deck() -> list[Card]:
    """Создать и перемешать колоду из 52 карт."""
    cards = [Card(rank=r, suit=s) for s in SUITS for r in RANKS]
    random.shuffle(cards)
    return cards


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

    hide_second=True — скрыть вторую карту (показать рубашку).
    """
    if hide_second and len(hand) >= 2:
        return f"{hand[0]}  🂠"
    return "  ".join(str(c) for c in hand)


def cards_to_dicts(cards: list[Card]) -> list[dict]:
    """Сериализация списка карт в список словарей для Redis."""
    return [{"rank": c.rank, "suit": c.suit} for c in cards]


def dicts_to_cards(dicts: list[dict]) -> list[Card]:
    """Десериализация списка словарей из Redis в список карт."""
    return [Card(rank=d["rank"], suit=d["suit"]) for d in dicts]
