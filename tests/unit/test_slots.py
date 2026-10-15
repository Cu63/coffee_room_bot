from fractions import Fraction

from bot.presentation.handlers.slots import _get_outcome

# Значения кубика 🎰 в Telegram: 1..64, 64 — «777»
VALUES = range(1, 65)


def test_jackpot_only_on_777():
    jackpots = [v for v in VALUES if _get_outcome(v)[0] == "jackpot"]
    assert jackpots == [64]


def test_three_of_a_kind():
    wins = [v for v in VALUES if _get_outcome(v)[0] == "win"]
    assert wins == [1, 22, 43]


def test_all_bet_uses_reduced_multipliers():
    assert _get_outcome(64, is_all=True)[1] < _get_outcome(64)[1]
    assert _get_outcome(1, is_all=True)[1] < _get_outcome(1)[1]


def test_return_to_player():
    # Ожидаемая выплата на единицу ставки (ставка уже списана): RTP < 1 — казино в плюсе
    rtp = sum(Fraction(_get_outcome(v)[1]) for v in VALUES) / 64
    rtp_all = sum(Fraction(_get_outcome(v, is_all=True)[1]) for v in VALUES) / 64
    assert rtp == Fraction(54, 64)
    assert rtp_all == Fraction(14, 64)
