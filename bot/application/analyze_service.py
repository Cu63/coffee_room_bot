from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from bot.application.interfaces.message_repository import ChatMessage, IMessageRepository
from bot.infrastructure.config_loader import AnalyzeConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)


def _format_messages(messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        name = f"@{msg.username}" if msg.username else msg.full_name
        ts = msg.sent_at.strftime("%H:%M %d.%m")
        safe_text = msg.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"[{ts}] {name}: {safe_text}")
    return "\n".join(lines)


@dataclass(slots=True)
class AnalyzeResult:
    text: str
    requested: int
    actual: int


class AnalyzeService:
    """Оркестрирует запросы /analyze и /wir."""

    def __init__(
        self,
        client: OpenAiClient,
        message_repo: IMessageRepository,
        config: AnalyzeConfig,
        formatter: MessageFormatter,
    ) -> None:
        self._client = client
        self._repo = message_repo
        self._config = config
        self._fmt = formatter

    def _warning(self, actual: int, requested: int) -> str:
        if actual >= requested:
            return ""
        return self._fmt._t["analyze_shortage_warning"].format(
            actual=actual, requested=requested
        )

    async def analyze(
        self,
        chat_id: int,
        n: int,
        user_ids: list[int] | None,
        since: datetime | None = None,
    ) -> AnalyzeResult:
        messages = await self._repo.get_recent_with_text(chat_id, n, user_ids, since)
        actual = len(messages)

        if actual == 0:
            return AnalyzeResult(
                text=self._fmt._t["analyze_no_messages"],
                requested=n,
                actual=0,
            )

        user_prompt = self._fmt._t["analyze_user_prompt"].format(
            warning=self._warning(actual, n),
            actual=actual,
            messages=_format_messages(messages),
        )
        resp = await self._client.chat([
            {"role": "system", "content": self._fmt._t["analyze_system_prompt"]},
            {"role": "user", "content": user_prompt},
        ])
        logger.info(
            "analyze: chat=%d n=%d actual=%d in=%d out=%d",
            chat_id, n, actual, resp.input_tokens, resp.output_tokens,
        )
        return AnalyzeResult(text=resp.text or "Нет ответа от модели.", requested=n, actual=actual)

    async def wir(
        self,
        chat_id: int,
        n: int,
        since: datetime | None = None,
    ) -> AnalyzeResult:
        messages = await self._repo.get_recent_with_text(chat_id, n, since=since)
        actual = len(messages)

        if actual == 0:
            return AnalyzeResult(
                text=self._fmt._t["analyze_no_messages"],
                requested=n,
                actual=0,
            )

        user_prompt = self._fmt._t["wir_user_prompt"].format(
            warning=self._warning(actual, n),
            actual=actual,
            messages=_format_messages(messages),
        )
        resp = await self._client.chat([
            {"role": "system", "content": self._fmt._t["wir_system_prompt"]},
            {"role": "user", "content": user_prompt},
        ])
        logger.info(
            "wir: chat=%d n=%d actual=%d in=%d out=%d",
            chat_id, n, actual, resp.input_tokens, resp.output_tokens,
        )
        return AnalyzeResult(text=resp.text or "Нет ответа от модели.", requested=n, actual=actual)