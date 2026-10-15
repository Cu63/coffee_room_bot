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
        ("50", (50, None)),
        ("10", (10, None)),
        ("100", (100, None)),
        ("abc", (None, "bet_not_positive")),
        ("0", (None, "bet_not_positive")),
        ("-5", (None, "bet_not_positive")),
        ("9", (None, "bet_too_small")),
        ("101", (None, "bet_too_large")),
    ],
)
def test_check_bet(formatter, raw, expected):
    bet, error = check_bet(raw, 10, 100, formatter)
    expected_bet, error_key = expected
    assert bet == expected_bet
    if error_key is None:
        assert error is None
    else:
        assert error.startswith(formatter._t[error_key].split("{")[0])
