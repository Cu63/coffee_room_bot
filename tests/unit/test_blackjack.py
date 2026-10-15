import pytest

from bot.application.blackjack_service import (
    Card,
    build_deck,
    cards_to_dicts,
    dicts_to_cards,
    format_hand,
    hand_score,
)
from bot.presentation.handlers.blackjack.helpers import _is_natural, _render_table, _winner


def test_hand_score_simple():
    hand = [Card("5", "♠️"), Card("7", "♥️")]
    assert hand_score(hand) == 12


def test_hand_score_ace_high():
    hand = [Card("A", "♠️"), Card("9", "♥️")]
    assert hand_score(hand) == 20


def test_hand_score_ace_reduces():
    hand = [Card("A", "♠️"), Card("9", "♥️"), Card("5", "♦️")]
    assert hand_score(hand) == 15  # 11+9+5=25 -> 1+9+5=15


def test_hand_score_two_aces():
    hand = [Card("A", "♠️"), Card("A", "♥️")]
    assert hand_score(hand) == 12  # 11+1


def test_hand_score_two_aces_and_nine():
    hand = [Card("A", "♠️"), Card("A", "♥️"), Card("9", "♣️")]
    assert hand_score(hand) == 21  # 11+1+9


def test_hand_score_three_aces_and_nine():
    hand = [Card("A", "♠️"), Card("A", "♥️"), Card("A", "♦️"), Card("9", "♣️")]
    assert hand_score(hand) == 12  # 11+1+1+9=22 — перебор, все тузы по 1


def test_hand_score_blackjack():
    hand = [Card("A", "♠️"), Card("K", "♥️")]
    assert hand_score(hand) == 21


def test_format_hand_normal():
    hand = [Card("A", "♠️"), Card("K", "♥️")]
    assert "A♠️" in format_hand(hand)
    assert "K♥️" in format_hand(hand)


def test_format_hand_hidden():
    hand = [Card("A", "♠️"), Card("K", "♥️")]
    result = format_hand(hand, hide_second=True)
    assert "A♠️" in result
    assert "🂠" in result
    assert "K♥️" not in result


def test_deck_has_52_unique_cards():
    deck = build_deck()
    assert len(deck) == 52
    assert len({(c.rank, c.suit) for c in deck}) == 52


def test_cards_dicts_roundtrip():
    cards = build_deck()[:5]
    assert dicts_to_cards(cards_to_dicts(cards)) == cards


def test_is_natural():
    assert _is_natural(cards_to_dicts([Card("A", "♠️"), Card("K", "♥️")]))
    assert not _is_natural(cards_to_dicts([Card("A", "♠️"), Card("5", "♥️"), Card("5", "♦️")]))


def test_table_with_hidden_hand_is_encodable():
    # Регрессия: рубашка карты была записана суррогатной парой, и edit_text падал
    data = {
        "p1_id": 1,
        "p1_name": "A",
        "p1_username": "",
        "p1_hand": cards_to_dicts([Card("A", "♠️"), Card("6", "♥️")]),
        "p2_id": 2,
        "p2_name": "B",
        "p2_username": "",
        "p2_hand": cards_to_dicts([Card("K", "♦️"), Card("Q", "♣️")]),
        "turn": "p1",
    }
    table = _render_table(data)
    assert "🂠" in table
    table.encode("utf-8")


def _hands(p1: list[Card], p2: list[Card], *, p1_busted: bool = False, p2_busted: bool = False) -> dict:
    return {
        "p1_hand": cards_to_dicts(p1),
        "p2_hand": cards_to_dicts(p2),
        "p1_busted": p1_busted,
        "p2_busted": p2_busted,
    }


ACE, KING, NINE, SEVEN, FIVE = Card("A", "♠️"), Card("K", "♥️"), Card("9", "♦️"), Card("7", "♣️"), Card("5", "♠️")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_hands([ACE, KING], [SEVEN, NINE, FIVE]), "p1"),  # блекджек бьёт обычные 21
        (_hands([SEVEN, NINE, FIVE], [ACE, KING]), "p2"),
        (_hands([ACE, KING], [KING, ACE]), None),  # два блекджека — ничья
        (_hands([KING, NINE], [KING, SEVEN]), "p1"),
        (_hands([KING, SEVEN], [KING, SEVEN]), None),
        (_hands([KING, NINE, FIVE], [FIVE, SEVEN], p1_busted=True), "p2"),  # перебор проигрывает меньшему
        (_hands([KING, NINE, FIVE], [KING, SEVEN, FIVE], p1_busted=True, p2_busted=True), None),
    ],
    ids=["natural-beats-21", "natural-p2", "two-naturals", "more-points", "equal", "bust-loses", "both-bust"],
)
def test_winner(data, expected):
    assert _winner(data) == expected
