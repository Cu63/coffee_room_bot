from bot.domain.reaction_registry import ReactionRegistry


def test_get_existing():
    reg = ReactionRegistry({"👍": 1, "❤️": 2})
    r = reg.get("👍")
    assert r is not None
    assert r.weight == 1


def test_get_with_variation_selector():
    reg = ReactionRegistry({"❤️": 2})
    # С и без U+FE0F должен находить
    assert reg.get("❤️") is not None
    assert reg.get("❤") is not None
    assert reg.get("❤").weight == 2


def test_get_missing():
    reg = ReactionRegistry({"👍": 1})
    assert reg.get("🎉") is None
