"""Безопасный HTML для Telegram: ответы LLM не ломают сообщение и не протаскивают чужие теги."""

import re

import pytest

from bot.presentation.html import sanitize_html, split_html


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("a < b & c > d", "a &lt; b &amp; c &gt; d"),
        ("<b>жирный</b> <span>текст</span> <script>alert(1)</script>", "<b>жирный</b> текст alert(1)"),
        ("<b>не закрыт", "<b>не закрыт</b>"),
        ("<b><i>крест</b></i>", "<b><i>крест</i></b>"),
        ("<strong>x</strong> <em>y</em> <del>z</del>", "<b>x</b> <i>y</i> <s>z</s>"),
        ("строка<br>ещё<p>абзац</p>", "строка\nещё\nабзац"),
        ('<a href="https://x.ru/?a=1&b=2">ссылка</a>', '<a href="https://x.ru/?a=1&amp;b=2">ссылка</a>'),
        ('<a href="tg://user?id=1">упоминание</a> <a href="javascript:x">js</a>', "упоминание js"),
        ('<pre><code class="language-python">x = 1 < 2</code></pre>', "<pre><code>x = 1 &lt; 2</code></pre>"),
        ("AT&amp;T", "AT&amp;T"),
    ],
    ids=[
        "specials",
        "unknown-tags",
        "unclosed",
        "crossed",
        "synonyms",
        "breaks",
        "link",
        "bad-links",
        "code",
        "entity",
    ],
)
def test_sanitize_html(raw, clean):
    assert sanitize_html(raw) == clean


def _balanced(part: str) -> bool:
    stack = []
    for closing, name in re.findall(r"<(/?)([a-z-]+)", part):
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def test_split_html_keeps_tags_balanced_and_text_whole():
    text = sanitize_html("начало " + "<b>" + "слово " * 300 + "</b>" + " <i>курсив " * 50 + "</i> конец")
    parts = split_html(text, limit=500)

    assert len(parts) > 1
    assert all(len(part) <= 500 and _balanced(part) for part in parts)
    plain = re.sub(r"<[^>]+>", "", "".join(parts))
    assert plain == re.sub(r"<[^>]+>", "", text)


def test_short_text_is_one_part():
    assert split_html("<b>коротко</b>") == ["<b>коротко</b>"]
