"""Генерация ежедневной сводки чата: чистка шума + map-reduce.

Модель сводки слабая (gpt-4.1-nano), поэтому ей нельзя показывать тысячи
реплик за раз — она путает авторство и выдумывает события. Решение:
1) выкинуть шум (команды, односложный мусор, дубли);
2) MAP — бить переписку на маленькие пачки и сворачивать каждую в сухой
   список фактов-тезисов (простая задача даже для слабой модели);
3) REDUCE — собрать все тезисы (их немного) и оформить финальное HTML-саммари.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator

from bot.application.analyze_service import _format_messages
from bot.application.interfaces.message_repository import ChatMessage
from bot.infrastructure.config_loader import DailySummaryConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)

_NO_ANSWER = "Нет ответа от модели."

# Односложные реплики без смысла для сводки.
_NOISE_WORDS: frozenset[str] = frozenset({
    "+", "++", "+1", "-", "ок", "окей", "ok", "ага", "угу", "да", "нет", "не",
    "лол", "kek", "кек", "ору", "хаха", "ахаха", "топ", "че", "чё", "ну",
    "хм", "ммм", "пон", "понял", "спс", "пж", "плюс", "ясно", "лан",
})


def _is_noise(text: str) -> bool:
    """Сообщение бесполезно для сводки?"""
    t = text.strip()
    if not t:
        return True
    if t.startswith("/"):  # команды боту
        return True
    if not re.search(r"\w", t, re.UNICODE):  # голые эмодзи/пунктуация
        return True
    if t.lower() in _NOISE_WORDS:
        return True
    if len(t) <= 2:  # совсем короткий обрывок
        return True
    return False


def _clean(messages: list[ChatMessage], admin_prefix: str) -> list[ChatMessage]:
    """Выкинуть шум и схлопнуть подряд идущие дубли одного автора."""
    prefix = admin_prefix.strip().lower()
    out: list[ChatMessage] = []
    prev_key: tuple[int, str] | None = None
    for m in messages:
        if _is_noise(m.text):
            continue
        if prefix and m.text.strip().lower().startswith(prefix):  # админ-команды
            continue
        key = (m.user_id, m.text.strip().lower())
        if key == prev_key:  # повтор «гг гг гг»
            continue
        prev_key = key
        out.append(m)
    return out


def _chunk(items: list[ChatMessage], size: int) -> Iterator[list[ChatMessage]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def generate_daily_summary(
    client: OpenAiClient,
    formatter: MessageFormatter,
    cfg: DailySummaryConfig,
    messages: list[ChatMessage],
    date_str: str,
    admin_prefix: str = "",
) -> str:
    """Сводка через map-reduce. Возвращает готовый HTML-текст."""
    cleaned = _clean(messages, admin_prefix)
    if not cleaned:
        return _NO_ANSWER

    t = formatter._t
    chunk_size = max(1, cfg.chunk_size)
    chunks = list(_chunk(cleaned, chunk_size))

    # ── MAP: каждую пачку сворачиваем в сухой список тезисов ──────────────
    notes: list[str] = []
    for idx, chunk in enumerate(chunks, 1):
        user_prompt = t["daily_summary_map_user_prompt"].format(
            date=date_str,
            messages=_format_messages(chunk),
        )
        resp = await client.chat(
            [
                {"role": "system", "content": t["daily_summary_map_system_prompt"]},
                {"role": "user", "content": user_prompt},
            ],
            temperature=cfg.temperature,
            max_tokens=cfg.map_max_tokens,
        )
        note = (resp.text or "").strip()
        if note:
            notes.append(note)
        logger.info(
            "daily_summary map: chunk %d/%d msgs=%d in=%d out=%d",
            idx, len(chunks), len(chunk), resp.input_tokens, resp.output_tokens,
        )

    if not notes:
        return _NO_ANSWER

    # Один кусок и так короткий — REDUCE всё равно нужен для оформления,
    # но это уже компактный вход, на котором слабая модель не плывёт.
    combined = "\n".join(notes)
    reduce_prompt = t["daily_summary_reduce_user_prompt"].format(
        date=date_str,
        notes=combined,
    )
    resp = await client.chat(
        [
            {"role": "system", "content": t["daily_summary_system_prompt"]},
            {"role": "user", "content": reduce_prompt},
        ],
        temperature=cfg.temperature,
    )
    logger.info(
        "daily_summary reduce: chunks=%d notes_chars=%d in=%d out=%d",
        len(chunks), len(combined), resp.input_tokens, resp.output_tokens,
    )
    return resp.text or _NO_ANSWER
