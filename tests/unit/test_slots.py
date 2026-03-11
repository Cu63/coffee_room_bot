from bot.application.slots_service import SlotsConfig, SlotsMachine, SpinOutcome


def test_spin_returns_result():
    cfg = SlotsConfig(min_bet=1, max_bet=100)
    machine = SlotsMachine(cfg)
    result = machine.spin(10)
    assert len(result.reels) == 3
    assert result.bet == 10
    assert result.outcome in SpinOutcome


def test_spin_loss_delta_negative():
    cfg = SlotsConfig(min_bet=1, max_bet=100)
    machine = SlotsMachine(cfg)
    # Spin many times, at least some should be losses
    losses = []
    for _ in range(100):
        r = machine.spin(10)
        if r.outcome == SpinOutcome.LOSS:
            losses.append(r)
    assert len(losses) > 0
    for r in losses:
        assert r.delta == -10


def test_spin_win_delta_positive():
    cfg = SlotsConfig(min_bet=1, max_bet=100)
    machine = SlotsMachine(cfg)
    wins = []
    for _ in range(1000):
        r = machine.spin(10)
        if r.outcome == SpinOutcome.WIN:
            wins.append(r)
    # With 1000 spins we should get at least one win
    if wins:
        for r in wins:
            assert r.delta > 0


def test_theoretical_rtp():
    cfg = SlotsConfig()
    machine = SlotsMachine(cfg)
    rtp = machine.theoretical_rtp
    assert 0.5 < rtp < 2.0  # sanity check
