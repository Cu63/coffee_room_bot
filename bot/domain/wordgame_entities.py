"""Доменные сущности игры «Угадайка»."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"^[a-zA-Zа-яА-ЯёЁ]+$")


def normalize_word(word: str) -> str:
    return word.strip().upper()


def is_valid_word(word: str, min_len: int = 2, max_len: int = 32) -> bool:
    return bool(_WORD_RE.fullmatch(word)) and min_len <= len(word) <= max_len


def compare(secret: str, guess: str) -> list[bool]:
    """True на каждой позиции, где буква совпала точно."""
    if len(secret) != len(guess):
        return []
    return [s == g for s, g in zip(secret.upper(), guess.upper())]


def merge_revealed(current: list[bool], matches: list[bool]) -> list[bool]:
    return [a or b for a, b in zip(current, matches)]


def format_masked(word: str, revealed: list[bool]) -> str:
    """К О · К А  — открытые буквы на месте, закрытые — точки."""
    chars = [c if revealed[i] else "·" for i, c in enumerate(word)]
    return "<code>" + " ".join(chars) + "</code>"


@dataclass
class WordGame:
    game_id: str
    chat_id: int
    creator_id: int
    word: str          # всегда UPPERCASE
    bet: int
    ends_at: float
    revealed: list[bool] = field(default_factory=list)
    guesses: list[dict] = field(default_factory=list)  # [{user_id, word}]
    message_id: int = 0                                 # ID игрового сообщения в группе
    finished: bool = False
    winner_id: int | None = None
    is_random: bool = False                             # True если слово загадал бот (/rword)

    def __post_init__(self) -> None:
        if not self.revealed:
            self.revealed = [False] * len(self.word)

    @property
    def masked(self) -> str:
        return format_masked(self.word, self.revealed)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.ends_at and not self.finished

    @property
    def revealed_count(self) -> int:
        return sum(self.revealed)

    def already_tried(self, user_id: int, word: str) -> bool:
        return any(
            g["user_id"] == user_id and g["word"] == word.upper()
            for g in self.guesses
        )