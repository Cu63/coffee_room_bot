from bot.application.blackjack_service import (
    BlackjackRound,
    Card,
    GameResult,
    format_hand,
    hand_score,
)


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


def test_payout_blackjack():
    rnd = BlackjackRound(player_id=1, chat_id=1, bet=100)
    rnd.result = GameResult.PLAYER_BLACKJACK
    assert rnd.payout_delta() == 150


def test_payout_win():
    rnd = BlackjackRound(player_id=1, chat_id=1, bet=100)
    rnd.result = GameResult.PLAYER_WIN
    assert rnd.payout_delta() == 100


def test_payout_loss():
    rnd = BlackjackRound(player_id=1, chat_id=1, bet=100)
    rnd.result = GameResult.DEALER_WIN
    assert rnd.payout_delta() == -100


def test_payout_push():
    rnd = BlackjackRound(player_id=1, chat_id=1, bet=100)
    rnd.result = GameResult.PUSH
    assert rnd.payout_delta() == 0


def test_deal_creates_hands():
    rnd = BlackjackRound(player_id=1, chat_id=1, bet=10)
    rnd.deal()
    assert len(rnd.player_hand) == 2
    assert len(rnd.dealer_hand) == 2
    assert len(rnd.deck) == 48


def test_hit_adds_card():
    rnd = BlackjackRound(player_id=1, chat_id=1, bet=10)
    rnd.deal()
    rnd.finished = False  # ensure not a natural blackjack
    initial = len(rnd.player_hand)
    if not rnd.finished:
        rnd.hit()
        assert len(rnd.player_hand) == initial + 1
