"""Общая проверка ставки для /bj, /ttt, /duel, /dice и /anagram."""

import pytest

from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import load_config, load_messages
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.utils import check_bet


@pytest.fixture(scope="module")
def formatter() -> MessageFormatter:
    score = load_config().score
    pluralizer = ScorePluralizer(score.singular, score.plural_few, score.plural_many, score.icon)
    return MessageFormatter(load_messages(), pluralizer)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("50", 50),
        ("10", 10),
        ("100", 100),
        ("abc", "bet_not_positive"),
        ("0", "bet_not_positive"),
        ("-5", "bet_not_positive"),
        ("9", "bet_too_small"),
        ("101", "bet_too_large"),
    ],
)
def test_check_bet(formatter, raw, expected):
    bet = check_bet(raw, 10, 100, formatter)
    if isinstance(expected, int):
        assert bet == expected
    else:
        # Отказ — текст для ответа
        assert isinstance(bet, str) and bet.startswith(formatter._t[expected].split("{")[0])
