from __future__ import annotations

import asyncio
import logging

import trafilatura
from trafilatura.settings import use_config

logger = logging.getLogger(__name__)

MAX_PAGE_CHARS = 5000
_FETCH_TIMEOUT = 5  # общий таймаут на одну страницу (секунды)

# Настраиваем trafilatura: короткие таймауты, без ретраев
_TRAF_CONFIG = use_config()
_TRAF_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "5")
_TRAF_CONFIG.set("DEFAULT", "MAX_REDIRECTS", "2")


def _fetch_sync(url: str) -> str:
    """Скачивает и извлекает основной текст страницы через trafilatura."""
    try:
        downloaded = trafilatura.fetch_url(url, config=_TRAF_CONFIG)
        if not downloaded:
            logger.warning("Failed to fetch %s", url)
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_precision=True,
        )
        if not text:
            logger.warning("No content extracted from %s", url)
            return ""
        return text[:MAX_PAGE_CHARS]
    except Exception:
        logger.warning("Error fetching %s", url, exc_info=True)
        return ""


async def fetch_page_text(url: str) -> str:
    """Асинхронно фетчит страницу с жёстким таймаутом."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_sync, url),
            timeout=_FETCH_TIMEOUT + 2,  # +2с запас к внутреннему таймауту
        )
    except asyncio.TimeoutError:
        logger.warning("Timeout fetching %s", url)
        return ""
