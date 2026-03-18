from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.application.interfaces.message_repository import ChatMessage, IMessageRepository
from bot.infrastructure.config_loader import AnalyzeConfig
from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)

# ── Системные промпты ────────────────────────────────────────────────────────

_ANALYZE_SYSTEM = (
    "Ты — аналитик переписки. Анализируй чат объективно и кратко. "
    "Отвечай на русском языке. "
    "Используй Telegram HTML-форматирование: <b>, <i>, <u>, <s>, <code>. "
    "ВСЕ открытые теги ОБЯЗАТЕЛЬНО закрывай."
)

_WIR_SYSTEM = (
    "Ты — беспристрастный арбитр конфликтов. "
    "Анализируй ситуацию честно, без симпатий к кому-либо. "
    "Отвечай на русском языке. "
    "Используй Telegram HTML-форматирование: <b>, <i>, <u>, <s>, <code>. "
    "ВСЕ открытые теги ОБЯЗАТЕЛЬНО закрывай."
)

_ANALYZE_PROMPT = """\
Проанализируй переписку участников Telegram-чата.

{warning}Переписка ({actual} сообщений):

{messages}

---
Задача:
— Кто о чём говорит, какие темы поднимаются
— Общее настроение и тон участников
— Интересные наблюдения о динамике общения
— Если анализируются конкретные пользователи — сосредоточься на них
"""

_WIR_PROMPT = """\
Проанализируй переписку участников Telegram-чата и разбери конфликт или ситуацию.

{warning}Переписка ({actual} сообщений):

{messages}

---
Задача — дай полный разбор:
1. <b>Суть ситуации</b>: что произошло, каковы позиции сторон
2. <b>Манипуляции и нечестные приёмы</b>: давление, газлайтинг, шантаж, угрозы, демагогия, оскорбления — у кого и где конкретно
3. <b>Логические ошибки</b>: соломенное чучело, переход на личности, ложные дихотомии и т.д.
4. <b>Вердикт</b>: кто прав, кто нет и почему — без дипломатии
5. <b>Как стоило поступить</b>: конкретные рекомендации каждой стороне
6. <b>Урегулирование</b>: если конфликт ещё не закрыт — как его разрешить
"""


# ── Вспомогательные функции ─────────────────────────────────────────────────

def _format_messages(messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        name = f"@{msg.username}" if msg.username else msg.full_name
        ts = msg.sent_at.strftime("%H:%M %d.%m")
        # Экранируем HTML в тексте пользователя, чтобы не сломать разметку ответа
        safe_text = msg.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"[{ts}] {name}: {safe_text}")
    return "\n".join(lines)


def _shortage_warning(actual: int, requested: int) -> str:
    if actual >= requested:
        return ""
    return f"⚠️ Собрано {actual} из {requested} запрошенных сообщений — в базе больше нет.\n\n"


# ── Результирующий тип ───────────────────────────────────────────────────────

@dataclass(slots=True)
class AnalyzeResult:
    text: str
    requested: int
    actual: int


# ── Сервис ───────────────────────────────────────────────────────────────────

class AnalyzeService:
    """Оркестрирует запросы /analyze и /wir."""

    def __init__(
        self,
        client: OpenAiClient,
        message_repo: IMessageRepository,
        config: AnalyzeConfig,
    ) -> None:
        self._client = client
        self._repo = message_repo
        self._config = config

    async def analyze(
        self,
        chat_id: int,
        n: int,
        user_ids: list[int] | None,
    ) -> AnalyzeResult:
        """Анализ последних N сообщений (всех или конкретных пользователей)."""
        messages = await self._repo.get_recent_with_text(chat_id, n, user_ids)
        actual = len(messages)

        if actual == 0:
            return AnalyzeResult(
                text="В базе не найдено сообщений для анализа.",
                requested=n,
                actual=0,
            )

        prompt = _ANALYZE_PROMPT.format(
            warning=_shortage_warning(actual, n),
            actual=actual,
            messages=_format_messages(messages),
        )
        resp = await self._client.chat(
            [
                {"role": "system", "content": _ANALYZE_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        logger.info(
            "analyze: chat=%d n=%d actual=%d in=%d out=%d",
            chat_id, n, actual,
            resp.input_tokens, resp.output_tokens,
        )
        return AnalyzeResult(text=resp.text or "Нет ответа от модели.", requested=n, actual=actual)

    async def wir(self, chat_id: int, n: int) -> AnalyzeResult:
        """Who Is Right — разбор конфликта по последним N сообщениям."""
        messages = await self._repo.get_recent_with_text(chat_id, n)
        actual = len(messages)

        if actual == 0:
            return AnalyzeResult(
                text="В базе не найдено сообщений для анализа.",
                requested=n,
                actual=0,
            )

        prompt = _WIR_PROMPT.format(
            warning=_shortage_warning(actual, n),
            actual=actual,
            messages=_format_messages(messages),
        )
        resp = await self._client.chat(
            [
                {"role": "system", "content": _WIR_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        logger.info(
            "wir: chat=%d n=%d actual=%d in=%d out=%d",
            chat_id, n, actual,
            resp.input_tokens, resp.output_tokens,
        )
        return AnalyzeResult(text=resp.text or "Нет ответа от модели.", requested=n, actual=actual)
