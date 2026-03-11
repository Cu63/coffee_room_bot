from bot.domain.emoji_utils import normalize_emoji


def test_removes_variation_selector():
    assert normalize_emoji("❤️") == "❤"
    assert normalize_emoji("\u2764\ufe0f") == "\u2764"


def test_no_variation_selector():
    assert normalize_emoji("🔥") == "🔥"
    assert normalize_emoji("👍") == "👍"


def test_empty():
    assert normalize_emoji("") == ""
