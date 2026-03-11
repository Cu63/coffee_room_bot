from bot.domain.pluralizer import ScorePluralizer


def _p() -> ScorePluralizer:
    return ScorePluralizer("балл", "балла", "баллов")


def test_singular():
    p = _p()
    assert p.pluralize(1) == "балл"
    assert p.pluralize(21) == "балл"
    assert p.pluralize(101) == "балл"


def test_few():
    p = _p()
    assert p.pluralize(2) == "балла"
    assert p.pluralize(3) == "балла"
    assert p.pluralize(4) == "балла"
    assert p.pluralize(22) == "балла"


def test_many():
    p = _p()
    assert p.pluralize(0) == "баллов"
    assert p.pluralize(5) == "баллов"
    assert p.pluralize(11) == "баллов"
    assert p.pluralize(12) == "баллов"
    assert p.pluralize(14) == "баллов"
    assert p.pluralize(19) == "баллов"
    assert p.pluralize(100) == "баллов"


def test_negative():
    p = _p()
    assert p.pluralize(-1) == "балл"
    assert p.pluralize(-5) == "баллов"
