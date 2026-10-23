"""/think: уровень мышления по IQ."""

import pytest

from bot.application.think_service import iq_tier
from bot.infrastructure.config_loader import load_messages

TEXTS = load_messages()


@pytest.mark.parametrize(
    ("iq", "tier"),
    [(0, "very_low"), (49, "very_low"), (50, "low"), (79, "low"), (80, "average"), (109, "average"),
     (110, "high"), (139, "high"), (140, "genius"), (199, "genius"), (200, "transcendent"), (255, "transcendent")],
)  # fmt: skip
def test_iq_tiers(iq, tier):
    assert iq_tier(iq) == tier
    assert f"think_style_{tier}" in TEXTS  # у каждого уровня есть описание для промпта
