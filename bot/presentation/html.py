"""Безопасный HTML для Telegram из текста, которому нельзя доверять (ответы LLM).

Telegram отклоняет сообщение целиком, если в HTML есть незакрытый или незнакомый тег,
а голый «<» или «&» в тексте ломает разметку. sanitize_html оставляет только теги,
которые понимает Telegram, экранирует всё остальное и закрывает открытые теги;
split_html режет длинный текст на части, не разрывая теги.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

# Теги Telegram и их синонимы
_ALLOWED = {
    "b": "b",
    "strong": "b",
    "i": "i",
    "em": "i",
    "u": "u",
    "ins": "u",
    "s": "s",
    "strike": "s",
    "del": "s",
    "code": "code",
    "pre": "pre",
    "blockquote": "blockquote",
    "tg-spoiler": "tg-spoiler",
    "a": "a",
}
_LINK_SCHEMES = {"http", "https"}
_BREAKS = {"br", "p", "li"}  # блочные теги превращаются в перевод строки


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BREAKS:
            self.parts.append("\n")
            return
        name = _ALLOWED.get(tag)
        if name is None:
            return
        if name == "a":
            href = dict(attrs).get("href") or ""
            if urlparse(href).scheme not in _LINK_SCHEMES:
                return  # tg://user, javascript: и прочее — ссылкой не делаем
            self.parts.append(f'<a href="{escape(href, quote=True)}">')
        else:
            self.parts.append(f"<{name}>")
        self.open.append(name)

    def handle_endtag(self, tag: str) -> None:
        name = _ALLOWED.get(tag)
        if name is None or name not in self.open:
            return
        # Закрываем всё, что открыто внутри, — Telegram не принимает перекрёстные теги
        while self.open:
            inner = self.open.pop()
            self.parts.append(f"</{inner}>")
            if inner == name:
                break

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data, quote=False))

    def result(self) -> str:
        return "".join(self.parts) + "".join(f"</{name}>" for name in reversed(self.open))


def sanitize_html(text: str) -> str:
    """HTML, который Telegram точно примет: только его теги, всё прочее — текстом."""
    parser = _Sanitizer()
    parser.feed(text)
    parser.close()
    return parser.result()


class _Splitter(HTMLParser):
    """Режет уже чистый HTML на куски до limit символов по границе слова, не разрывая теги."""

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=False)
        self.limit = limit
        self.chunks: list[str] = []
        self.current = ""
        self.open: list[tuple[str, str]] = []  # (имя, открывающий тег)

    def _closing(self) -> str:
        return "".join(f"</{name}>" for name, _ in reversed(self.open))

    def _reopening(self) -> str:
        return "".join(tag for _, tag in self.open)

    def _flush(self) -> None:
        self.chunks.append(self.current + self._closing())
        self.current = self._reopening()

    def _append(self, piece: str) -> None:
        if len(self.current) + len(piece) + len(self._closing()) > self.limit and self.current.strip():
            self._flush()
        self.current += piece

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or f"<{tag}>"
        self._append(raw)
        self.open.append((tag, raw))

    def handle_endtag(self, tag: str) -> None:
        self.current += f"</{tag}>"
        for i in range(len(self.open) - 1, -1, -1):
            if self.open[i][0] == tag:
                del self.open[i]
                break

    def handle_data(self, data: str) -> None:
        for word in _words(data):
            self._append(escape(word, quote=False))

    def handle_entityref(self, name: str) -> None:
        self._append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._append(f"&#{name};")


def _words(text: str) -> list[str]:
    """Слова вместе с пробелами после них: режем только между словами."""
    words: list[str] = []
    word = ""
    for ch in text:
        word += ch
        if ch.isspace():
            words.append(word)
            word = ""
    if word:
        words.append(word)
    return words


def split_html(text: str, limit: int = 4096) -> list[str]:
    """Части чистого HTML не длиннее limit: открытые теги закрываются в конце части и открываются в следующей."""
    if len(text) <= limit:
        return [text]
    splitter = _Splitter(limit)
    splitter.feed(text)
    splitter.close()
    if splitter.current.strip():
        splitter.chunks.append(splitter.current + splitter._closing())
    return [chunk for chunk in splitter.chunks if chunk.strip()]
