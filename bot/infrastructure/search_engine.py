from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

from bot.infrastructure.page_fetcher import fetch_page_text

logger = logging.getLogger(__name__)

_BLOCKED_DOMAINS = frozenset({
    "youtube.com", "youtu.be", "rutube.ru", "vk.com", "vkvideo.ru",
    "mvideo.ru", "citilink.ru",
})

_SEARCH_TIMEOUT = aiohttp.ClientTimeout(total=90)


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str = ""  # извлечённый текст страницы (заполняется после fetch)
    engine: str = ""   # google / yandex


class SearchEngine:
    """Поиск через OpenSERP (self-hosted, Google/Yandex)."""

    def __init__(self, base_url: str = "http://openserp:7000") -> None:
        self._base_url = base_url.rstrip("/")

    async def _query_openserp(
        self, engine: str, query: str, limit: int,
    ) -> list[SearchResult]:
        """Запрос к OpenSERP API."""
        url = f"{self._base_url}/{engine}/search"
        params = {"text": query, "lang": "RU", "limit": str(limit)}
        try:
            async with aiohttp.ClientSession(timeout=_SEARCH_TIMEOUT) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("OpenSERP %s returned %d", engine, resp.status)
                        return []
                    data = await resp.json()
        except Exception:
            logger.exception("OpenSERP %s request failed", engine)
            return []

        results: list[SearchResult] = []
        for item in data or []:
            r_url = item.get("url", "")
            if not r_url:
                continue
            results.append(SearchResult(
                title=item.get("title", ""),
                url=r_url,
                snippet=item.get("description", ""),
                engine=engine,
            ))
        return results

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Ищет через Google с одним retry при неудаче."""
        results = await self._query_openserp("google", query, max_results)
        if not results:
            logger.info("Google returned 0 results, retrying once...")
            await asyncio.sleep(2)
            results = await self._query_openserp("google", query, max_results)
        results = results[:max_results]
        logger.info("OpenSERP search for %r returned %d results", query, len(results))
        return results

    # ── Search + Content Extraction ──────────────────────────────────

    async def search_with_content(
        self, query: str, max_results: int = 5, max_fetch: int = 3,
    ) -> list[SearchResult]:
        """Поиск + извлечение контента из топ страниц через trafilatura."""
        results = await self.search(query, max_results)
        if not results:
            return results

        # Выбираем страницы для фетча (пропуская заблокированные домены)
        fetchable: list[tuple[int, str]] = []
        for i, r in enumerate(results):
            domain = urlparse(r.url).netloc.removeprefix("www.")
            if any(domain.endswith(bd) for bd in _BLOCKED_DOMAINS):
                continue
            fetchable.append((i, r.url))
            if len(fetchable) >= max_fetch:
                break

        # Фетчим параллельно
        if fetchable:
            tasks = [fetch_page_text(url) for _, url in fetchable]
            texts = await asyncio.gather(*tasks, return_exceptions=True)
            for (idx, _url), text in zip(fetchable, texts):
                if isinstance(text, Exception):
                    logger.warning("Failed to fetch %s: %s", _url, text)
                    continue
                if text and len(text.strip()) >= 100:
                    results[idx].content = text
                    logger.info("Fetched %d chars from %s", len(text), _url)

        fetched = sum(1 for r in results if r.content)
        logger.info(
            "search_with_content for %r: %d results, %d pages fetched",
            query, len(results), fetched,
        )
        return results

    # ── Форматирование ───────────────────────────────────────────────

    @staticmethod
    def format_context(results: list[SearchResult], include_content: bool = True) -> str:
        """Форматирует результаты поиска в компактный текст для LLM-контекста."""
        if not results:
            return ""
        parts = []
        for i, r in enumerate(results, 1):
            domain = urlparse(r.url).netloc.removeprefix("www.")
            section = f"[{i}] {r.title}\nURL: {r.url}\nИсточник: {domain}\n{r.snippet}"
            if include_content and r.content:
                section += f"\n\nИзвлечённый текст:\n{r.content[:4000]}"
            parts.append(section)
        return "\n\n".join(parts)
