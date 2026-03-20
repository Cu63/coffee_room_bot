from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from bot.application.interfaces.llm_repository import ILlmRepository
from bot.application.interfaces.message_repository import ChatMessage, IMessageRepository
from bot.infrastructure.config_loader import AnalyzeConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)

# Названия команд для фильтрации в llm_requests
_ANALYZE_COMMANDS: tuple[str, ...] = ("analyze", "wir")


def _format_messages(messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        name = f"@{msg.username}" if msg.username else msg.full_name
        ts = msg.sent_at.strftime("%H:%M %d.%m")
        safe_text = msg.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"[{ts}] {name}: {safe_text}")
    return "\n".join(lines)


class AnalyzeRateLimitExceeded(Exception):
    """Превышен дневной лимит токенов для /analyze и /wir."""

    def __init__(self, used: int, limit: int) -> None:
        self.used = used
        self.limit = limit
        super().__init__(f"daily input-token limit exceeded: {used}/{limit}")


@dataclass(slots=True)
class AnalyzeResult:
    text: str
    requested: int
    actual: int
    input_tokens: int = 0
    output_tokens: int = 0


class AnalyzeService:
    """Оркестрирует запросы /analyze и /wir.

    Добавляет:
    - проверку дневного лимита input-токенов per-user (исключение AnalyzeRateLimitExceeded)
    - логирование каждого запроса в llm_requests
    - доступ к глобальной статистике токенов за день
    - байпас лимитов для пользователей из списка администраторов
    """

    def __init__(
        self,
        client: OpenAiClient,
        message_repo: IMessageRepository,
        llm_repo: ILlmRepository,
        config: AnalyzeConfig,
        formatter: MessageFormatter,
        admin_users: list[str],
    ) -> None:
        self._client = client
        self._repo = message_repo
        self._llm_repo = llm_repo
        self._config = config
        self._fmt = formatter
        self._admin_users: set[str] = {u.lower() for u in admin_users}

    # ── Вспомогательные методы ───────────────────────────────────────────

    def _is_admin(self, username: str | None) -> bool:
        return username is not None and username.lower() in self._admin_users

    async def _check_token_limit(self, user_id: int, username: str | None) -> None:
        """Проверяет дневной лимит input-токенов. Raises AnalyzeRateLimitExceeded."""
        limit = self._config.daily_input_token_limit
        if limit <= 0:
            return  # 0 = без лимита
        if self._is_admin(username):
            return  # администраторы не ограничены
        used = await self._llm_repo.sum_input_tokens_today(user_id, _ANALYZE_COMMANDS)
        if used >= limit:
            raise AnalyzeRateLimitExceeded(used=used, limit=limit)

    async def _log(
        self,
        user_id: int,
        chat_id: int,
        command: str,
        query: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        try:
            await self._llm_repo.log_request(
                user_id=user_id,
                chat_id=chat_id,
                command=command,
                query=query,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception:
            logger.exception("analyze: не удалось залогировать запрос")

    def _warning(self, actual: int, requested: int) -> str:
        if actual >= requested:
            return ""
        return self._fmt._t["analyze_shortage_warning"].format(
            actual=actual, requested=requested
        )

    # ── Публичный метод: глобальная статистика токенов за день ───────────

    async def get_global_tokens_today(self) -> tuple[int, int]:
        """Возвращает (input_total, output_total) по всем пользователям за сегодня."""
        return await self._llm_repo.sum_tokens_global_today(_ANALYZE_COMMANDS)

    # ── /analyze ─────────────────────────────────────────────────────────

    async def analyze(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        n: int,
        user_ids: list[int] | None,
        since: datetime | None = None,
    ) -> AnalyzeResult:
        await self._check_token_limit(user_id, username)

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
            "analyze: chat=%d user=%d n=%d actual=%d in=%d out=%d",
            chat_id, user_id, n, actual, resp.input_tokens, resp.output_tokens,
        )

        await self._log(
            user_id=user_id,
            chat_id=chat_id,
            command="analyze",
            query=f"n={n} actual={actual}",
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )

        return AnalyzeResult(
            text=resp.text or "Нет ответа от модели.",
            requested=n,
            actual=actual,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )

    # ── /wir ─────────────────────────────────────────────────────────────

    async def wir(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        n: int,
        since: datetime | None = None,
    ) -> AnalyzeResult:
        await self._check_token_limit(user_id, username)

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
            "wir: chat=%d user=%d n=%d actual=%d in=%d out=%d",
            chat_id, user_id, n, actual, resp.input_tokens, resp.output_tokens,
        )

        await self._log(
            user_id=user_id,
            chat_id=chat_id,
            command="wir",
            query=f"n={n} actual={actual}",
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )

        return AnalyzeResult(
            text=resp.text or "Нет ответа от модели.",
            requested=n,
            actual=actual,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
