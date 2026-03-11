from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bot.application.interfaces.llm_repository import ILlmRepository
from bot.infrastructure.aitunnel_client import AiTunnelClient
from bot.infrastructure.search_engine import SearchEngine

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LlmResult:
    text: str
    used_today: int = 0
    daily_limit: int = 0
    is_admin: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    debug_trace: str = ""


class RateLimitExceeded(Exception):
    pass


class LlmService:
    """Оркестрирует LLM-запросы и поиск."""

    def __init__(
        self,
        client: AiTunnelClient,
        search_engine: SearchEngine,
        llm_repo: ILlmRepository,
        system_prompt: str,
        search_system_prompt: str,
        daily_limit: int,
        search_max_results: int,
        admin_users: list[str],
    ) -> None:
        self._client = client
        self._search = search_engine
        self._repo = llm_repo
        self._system_prompt = system_prompt
        self._search_system_prompt = search_system_prompt
        self._daily_limit = daily_limit
        self._search_max_results = search_max_results
        self._admin_users = set(admin_users)

    def _is_admin(self, username: str | None) -> bool:
        return username is not None and username.lower() in self._admin_users

    async def _check_limit(self, user_id: int, username: str | None) -> None:
        if self._is_admin(username):
            return
        count = await self._repo.count_today(user_id)
        if count >= self._daily_limit:
            raise RateLimitExceeded

    async def _make_result(self, text: str, user_id: int, username: str | None,
                           input_tokens: int, output_tokens: int) -> LlmResult:
        used = await self._repo.count_today(user_id)
        return LlmResult(
            text=text,
            used_today=used,
            daily_limit=self._daily_limit,
            is_admin=self._is_admin(username),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def ask(self, user_id: int, chat_id: int, username: str | None, question: str) -> LlmResult:
        await self._check_limit(user_id, username)
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": question},
        ]
        resp = await self._client.chat(messages)
        await self._repo.log_request(
            user_id=user_id,
            chat_id=chat_id,
            command="llm",
            query=question,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        return await self._make_result(resp.text, user_id, username, resp.input_tokens, resp.output_tokens)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _dedup_text(text: str) -> str:
        """Убирает дубликат, если LLM повторил текст дважды подряд."""
        text = text.strip()
        half = len(text) // 2
        margin = max(50, half // 10)
        for offset in range(margin):
            for pos in (half + offset, half - offset):
                if 0 < pos < len(text):
                    first = text[:pos].strip()
                    second = text[pos:].strip()
                    if first == second:
                        logger.info("Dedup: removed duplicate text (%d chars)", len(first))
                        return first
        return text

    # ── Main search flow ─────────────────────────────────────────────

    async def search_and_answer(
        self, user_id: int, chat_id: int, username: str | None, query: str,
        *, debug: bool = False,
    ) -> LlmResult:
        await self._check_limit(user_id, username)

        trace_parts: list[str] = []

        # ── Search + fetch pages ──────────────────────────────────
        results = await self._search.search_with_content(
            query, max_results=self._search_max_results, max_fetch=5,
        )

        if debug:
            trace_parts.append(f"=== QUERY: {query} ===")
            trace_parts.append(f"=== SEARCH: {len(results)} results ===")
            for r in results:
                status = f"content: {len(r.content)} chars" if r.content else "snippet only"
                trace_parts.append(f"  [{r.engine}] {r.title} | {r.url} ({status})")

        context_text = SearchEngine.format_context(results, include_content=True)

        if debug:
            trace_parts.append(f"\n{'='*60}")
            trace_parts.append(f"=== LLM CONTEXT [{len(context_text)} chars] ===\n{context_text}")

        # ── Single LLM call: answer WITH links ───────────────────
        messages = [
            {"role": "system", "content": self._search_system_prompt},
            {"role": "user", "content": f"Запрос пользователя: {query}\n\nРезультаты поиска:\n{context_text}"},
        ]

        resp = await self._client.chat(messages)
        text = resp.text or "Не удалось получить ответ."

        if debug:
            trace_parts.append(f"\n{'='*60}")
            trace_parts.append(f"=== LLM: in={resp.input_tokens} out={resp.output_tokens} ===")
            trace_parts.append(f"=== OUTPUT [{len(text)} chars] ===\n{text}")

        text = self._dedup_text(text)

        if debug:
            trace_parts.append(f"\n{'='*60}")
            trace_parts.append(f"=== FINAL [{len(text)} chars] ===\n{text}")
            trace_parts.append(f"\n=== TOTALS: input_tokens={resp.input_tokens} output_tokens={resp.output_tokens} ===")

        logger.info("Final response length: %d", len(text))

        await self._repo.log_request(
            user_id=user_id,
            chat_id=chat_id,
            command="search",
            query=query,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        llm_result = await self._make_result(text, user_id, username, resp.input_tokens, resp.output_tokens)
        if debug:
            llm_result.debug_trace = "\n".join(trace_parts)
        return llm_result
