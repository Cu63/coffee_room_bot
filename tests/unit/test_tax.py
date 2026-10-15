import pytest

from bot.infrastructure.config_loader import TaxBracket, TaxConfig
from bot.infrastructure.tax_loop import _compute_tax

TAX = TaxConfig(
    brackets=[
        TaxBracket(threshold=10000, percent=3),
        TaxBracket(threshold=20000, percent=5),
        TaxBracket(threshold=50000, percent=10),
    ]
)


@pytest.mark.parametrize(
    ("balance", "tax"),
    [
        (-500, 0),
        (0, 0),
        (10000, 0),  # порог не облагается
        (10001, 1),  # округление вверх
        (10100, 3),
        (20000, 300),  # первая полоса целиком
        (25000, 300 + 250),
        (60000, 300 + 1500 + 1000),
    ],
)
def test_progressive_tax(balance, tax):
    assert _compute_tax(balance, TAX) == tax


def test_richer_never_pays_less():
    taxes = [_compute_tax(balance, TAX) for balance in range(0, 70000, 7)]
    assert taxes == sorted(taxes)


def test_bracket_order_in_config_does_not_matter():
    reversed_brackets = TaxConfig(brackets=list(reversed(TAX.brackets)))
    assert _compute_tax(60000, reversed_brackets) == _compute_tax(60000, TAX)


def test_no_brackets_no_tax():
    assert _compute_tax(10**9, TaxConfig(brackets=[])) == 0
