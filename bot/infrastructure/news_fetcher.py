"""Получение случайной новости из RSS-лент российских СМИ.

Источники — крупные российские издания, НЕ являющиеся
иностранными агентами и НЕ заблокированные в РФ.
Парсинг через stdlib xml.etree + aiohttp (без новых зависимостей).
"""

from __future__ import annotations

import logging
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape

import aiohttp

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=10)


@dataclass(slots=True)
class NewsItem:
    """Одна новость из RSS-ленты."""

    title: str
    url: str
    source: str
    description: str = ""


# ── RSS-ленты ────────────────────────────────────────────────────
# Каждый элемент: (название источника, URL RSS-ленты)

DEFAULT_FEEDS: list[tuple[str, str]] = [
    ("ТАСС", "https://tass.com/rss/v2.xml"),
    ("РИА Новости", "https://ria.ru/export/rss2/archive/index.xml"),
    ("Lenta.ru", "https://lenta.ru/rss"),
    ("Известия", "https://iz.ru/xml/rss/all.xml"),
    ("Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml"),
    ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Газета.ru", "https://www.gazeta.ru/export/rss/lenta.xml"),
]


def _clean_html(text: str) -> str:
    """Грубая очистка HTML-тегов и HTML-entities из description."""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_rss(raw_xml: str, source_name: str) -> list[NewsItem]:
    """Парсит RSS/Atom XML и возвращает список новостей."""
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        logger.warning("Не удалось распарсить XML от %s", source_name)
        return items

    # RSS 2.0: channel/item
    for item_el in root.iter("item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        desc = _clean_html(item_el.findtext("description") or "")
        if title and link:
            items.append(NewsItem(title=title, url=link, source=source_name, description=desc))

    # Atom: entry
    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("atom:title", "", ns) or entry.findtext("title") or "").strip()
            link_el = entry.find("atom:link[@href]", ns) or entry.find("link[@href]")
            link = link_el.get("href", "").strip() if link_el is not None else ""
            desc = _clean_html(
                entry.findtext("atom:summary", "", ns) or entry.findtext("summary") or ""
            )
            if title and link:
                items.append(NewsItem(title=title, url=link, source=source_name, description=desc))

    return items


async def _fetch_feed(
    session: aiohttp.ClientSession,
    source_name: str,
    url: str,
) -> list[NewsItem]:
    """Загружает и парсит одну RSS-ленту."""
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning("RSS %s вернул %d", source_name, resp.status)
                return []
            raw = await resp.text(errors="replace")
    except Exception:
        logger.warning("Не удалось загрузить RSS %s", source_name, exc_info=True)
        return []
    return _parse_rss(raw, source_name)


async def fetch_random_news(
    feeds: list[tuple[str, str]] | None = None,
    max_items: int = 1,
) -> list[NewsItem]:
    """Загружает случайную RSS-ленту и возвращает до *max_items* случайных новостей.

    Если первая попытка не удалась, пробует ещё одну случайную ленту.
    """
    feeds = feeds or DEFAULT_FEEDS
    attempts = min(3, len(feeds))
    tried: set[int] = set()

    async with aiohttp.ClientSession(timeout=_FETCH_TIMEOUT) as session:
        for _ in range(attempts):
            idx = random.choice([i for i in range(len(feeds)) if i not in tried])
            tried.add(idx)
            source_name, url = feeds[idx]
            items = await _fetch_feed(session, source_name, url)
            if items:
                return random.sample(items, min(max_items, len(items)))

    logger.warning("Не удалось получить новости ни из одного источника")
    return []
