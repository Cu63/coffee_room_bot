"""Получение IT-новости из RSS-лент с LLM-фильтрацией и переводом.

Алгоритм:
1. Берём случайную ленту из config.yaml (news.feeds).
2. Собираем до CANDIDATE_COUNT статей.
3. [use_llm=true] OpenAiClient выбирает лучшую IT/позитивную новость,
   возвращает JSON {index, title} — заголовок уже на русском.
4. [translate=true] Если оригинальный description не на русском —
   отдельный вызов OpenAiClient переводит его.
5. Возвращаем NewsItem.

Используется OpenAiClient (analyze/proxyapi), а не AiTunnelClient.
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
    from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Сколько кандидатов передаём LLM для выбора
CANDIDATE_COUNT = 12

# Русские источники — перевод не нужен
_RU_SOURCES = {"habr", "хакер", "3dnews", "ixbt", "открытые системы"}

# ── Промпты ────────────────────────────────────────────────────────────────

_FILTER_SYSTEM = (
    "Ты — редактор IT-новостного бота в Telegram-чате технарей. "
    "Тебе дают пронумерованный список новостей. Твоя задача:\n"
    "1. Выбери ОДНУ новость, которая лучше всего подходит под критерии:\n"
    "   — Тематика: технологии, IT, ИИ, гаджеты, программирование, стартапы, наука.\n"
    "   — Тональность: нейтральная или позитивная. "
    "Избегай катастроф, войн, политики, уголовных дел, кризисов.\n"
    "2. Верни ТОЛЬКО JSON без markdown:\n"
    '{"index": <номер (0-based)>, '
    '"title": "<заголовок на русском, живой и лаконичный, до 100 символов>", '
    '"is_russian": <true если исходный заголовок/текст уже на русском, иначе false>}\n'
    "Если IT-новостей нет — выбери наименее негативную, перепиши заголовок нейтрально."
)

_TRANSLATE_SYSTEM = (
    "Ты — переводчик для IT-новостного бота. "
    "Переведи текст на русский язык точно и лаконично. "
    "Сохраняй технические термины. "
    "Верни ТОЛЬКО перевод, без пояснений, без кавычек."
)


@dataclass(slots=True)
class NewsItem:
    """Одна новость из RSS-ленты."""

    title: str
    url: str
    source: str
    description: str = ""


def _clean_html(text: str) -> str:
    """Очищает HTML-теги и HTML-entities."""
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

    # RSS 2.0
    for item_el in root.iter("item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        desc = _clean_html(item_el.findtext("description") or "")
        if title and link:
            items.append(NewsItem(title=title, url=link, source=source_name, description=desc))

    # Atom
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


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


async def _llm_pick(
    candidates: list[NewsItem],
    llm: "OpenAiClient",
) -> tuple[NewsItem, bool]:
    """Выбирает лучшую IT-позитивную новость через LLM.

    Возвращает (NewsItem с русским заголовком, is_russian).
    При ошибке — первый кандидат, is_russian=False.
    """
    numbered = "\n".join(
        f"{i}. {item.title}" + (f" — {item.description[:120]}" if item.description else "")
        for i, item in enumerate(candidates)
    )
    try:
        resp = await llm.chat([
            {"role": "system", "content": _FILTER_SYSTEM},
            {"role": "user", "content": f"Список новостей:\n{numbered}"},
        ])
        data = json.loads(_strip_json_fences(resp.text or ""))
        idx = int(data["index"])
        new_title = str(data.get("title", "")).strip()
        is_russian = bool(data.get("is_russian", False))
        chosen = candidates[idx]
        if new_title:
            chosen = NewsItem(
                title=new_title,
                url=chosen.url,
                source=chosen.source,
                description=chosen.description,
            )
        return chosen, is_russian
    except Exception:
        logger.warning("news: LLM filter failed, using first candidate", exc_info=True)
        return candidates[0], False


async def _llm_translate(text: str, llm: "OpenAiClient") -> str:
    """Переводит текст на русский через LLM. При ошибке — оригинал."""
    if not text.strip():
        return text
    try:
        resp = await llm.chat([
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user", "content": text},
        ])
        translated = (resp.text or "").strip()
        return translated if translated else text
    except Exception:
        logger.warning("news: перевод не удался, оставляем оригинал", exc_info=True)
        return text


def _source_is_russian(source_name: str) -> bool:
    return source_name.lower() in _RU_SOURCES


async def fetch_random_news(
    feeds: list[tuple[str, str]],
    llm: "OpenAiClient | None" = None,
    translate: bool = False,
    max_items: int = 1,
) -> list[NewsItem]:
    """Загружает RSS, фильтрует через LLM и при необходимости переводит.

    Параметры:
        feeds     — список (name, url) из config.yaml → news.feeds
        llm       — OpenAiClient; если None, LLM-шаги пропускаются
        translate — переводить ли description (и title если не RU-источник)
        max_items — сколько новостей вернуть (только без LLM)
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

            candidates = random.sample(items, min(CANDIDATE_COUNT, len(items)))

            if llm is not None:
                chosen, is_russian = await _llm_pick(candidates, llm)

                # Перевод description, если нужно и источник иностранный
                needs_translation = (
                    translate
                    and not is_russian
                    and not _source_is_russian(source_name)
                )
                if needs_translation and chosen.description:
                    translated_desc = await _llm_translate(chosen.description, llm)
                    chosen = NewsItem(
                        title=chosen.title,
                        url=chosen.url,
                        source=chosen.source,
                        description=translated_desc,
                    )

                return [chosen]

            # Без LLM — случайная новость без перевода
            return random.sample(items, min(max_items, len(items)))

    logger.warning("news: не удалось получить новости ни из одного источника")
    return []
