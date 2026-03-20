"""Получение IT-новости из RSS-лент с LLM-фильтрацией.

Алгоритм:
1. Берём случайную ленту из config.yaml (news.feeds).
2. Вытаскиваем из неё до CANDIDATE_COUNT статей.
3. Просим LLM выбрать самую IT-релевантную и позитивную.
4. Возвращаем одну NewsItem с заголовком, выправленным LLM.
"""

from __future__ import annotations

import json
import logging
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from bot.infrastructure.aitunnel_client import AiTunnelClient

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Сколько кандидатов передаём LLM для выбора
CANDIDATE_COUNT = 12

# ── Системный промпт для LLM-фильтра ──────────────────────────────────────
_FILTER_SYSTEM = (
    "Ты — редактор IT-новостного бота в Telegram-чате технарей. "
    "Тебе дают список новостей. Твоя задача:\n"
    "1. Выбери ОДНУ новость, которая лучше всего подходит под критерии:\n"
    "   — Тематика: технологии, IT, ИИ, гаджеты, программирование, стартапы, наука.\n"
    "   — Тональность: нейтральная или позитивная. "
    "Избегай катастроф, войн, политики, уголовных дел, кризисов.\n"
    "2. Верни ТОЛЬКО JSON-объект (без markdown, без пояснений):\n"
    '{"index": <номер выбранной новости (0-based)>, '
    '"title": "<заголовок на русском, живой и лаконичный, до 100 символов>"}\n'
    "Если IT-новостей нет вообще — выбери наименее негативную и перепиши заголовок "
    "так, чтобы он звучал нейтрально."
)


@dataclass(slots=True)
class NewsItem:
    """Одна новость из RSS-ленты."""

    title: str
    url: str
    source: str
    description: str = ""


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


async def _llm_pick(
    candidates: list[NewsItem],
    llm: "AiTunnelClient",
) -> NewsItem:
    """Просит LLM выбрать лучшую IT-позитивную новость из кандидатов.

    Возвращает NewsItem с заголовком, при необходимости перефразированным LLM.
    При любой ошибке — возвращает первого кандидата без изменений.
    """
    numbered = "\n".join(
        f"{i}. {item.title}" + (f" — {item.description[:120]}" if item.description else "")
        for i, item in enumerate(candidates)
    )
    user_prompt = f"Список новостей:\n{numbered}"

    try:
        resp = await llm.chat([
            {"role": "system", "content": _FILTER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ])
        raw = (resp.text or "").strip()
        # Вырезаем JSON, если LLM обернул в ```
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        idx = int(data["index"])
        new_title = str(data.get("title", "")).strip()
        chosen = candidates[idx]
        if new_title:
            chosen = NewsItem(
                title=new_title,
                url=chosen.url,
                source=chosen.source,
                description=chosen.description,
            )
        return chosen
    except Exception:
        logger.warning("news: LLM filter failed, using first candidate", exc_info=True)
        return candidates[0]


async def fetch_random_news(
    feeds: list[tuple[str, str]],
    llm: "AiTunnelClient | None" = None,
    max_items: int = 1,
) -> list[NewsItem]:
    """Загружает RSS, фильтрует через LLM и возвращает до *max_items* новостей.

    Параметры:
        feeds     — список (name, url), берётся из config.yaml → news.feeds
        llm       — клиент AiTunnelClient; если None, LLM-фильтрация пропускается
        max_items — сколько новостей вернуть (без LLM)
    """
    if not feeds:
        logger.warning("news: список фидов пуст — проверьте news.feeds в config.yaml")
        return []

    attempts = min(3, len(feeds))
    tried: set[int] = set()

    async with aiohttp.ClientSession(timeout=_FETCH_TIMEOUT) as session:
        for _ in range(attempts):
            idx = random.choice([i for i in range(len(feeds)) if i not in tried])
            tried.add(idx)
            source_name, url = feeds[idx]
            items = await _fetch_feed(session, source_name, url)
            if not items:
                continue

            # Собираем кандидатов для LLM
            candidates = random.sample(items, min(CANDIDATE_COUNT, len(items)))

            if llm is not None and len(candidates) > 1:
                chosen = await _llm_pick(candidates, llm)
                return [chosen]

            # Без LLM — просто случайная новость
            return random.sample(items, min(max_items, len(items)))

    logger.warning("news: не удалось получить новости ни из одного источника")
    return []
