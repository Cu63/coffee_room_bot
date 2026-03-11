from __future__ import annotations

import logging
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string


@dataclass(slots=True)
class LlmResponse:
    text: str
    input_tokens: int
    output_tokens: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict | None = None


class AiTunnelClient:
    """HTTP-клиент для OpenAI-совместимого API aitunnel.ru."""

    def __init__(self, api_key: str, base_url: str, model: str, max_output_tokens: int) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._session

    _MAX_RETRIES = 2

    async def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        tools: list[dict] | None = None,
    ) -> LlmResponse:
        session = await self._get_session()
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools

        for attempt in range(self._MAX_RETRIES):
            async with session.post(f"{self._base_url}/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()

            choice_raw = data["choices"][0]
            finish_reason = choice_raw.get("finish_reason")
            choice = choice_raw["message"]
            usage = data.get("usage", {})
            logger.info("LLM finish_reason=%s, content=%s, tool_calls=%s",
                         finish_reason,
                         repr((choice.get("content") or "")[:100]),
                         len(choice.get("tool_calls", [])))

            if finish_reason == "error" and attempt < self._MAX_RETRIES - 1:
                logger.warning("LLM returned error, retrying (attempt %d)", attempt + 1)
                continue
            break

        tool_calls: list[ToolCall] = []
        for tc in choice.get("tool_calls", []):
            tool_calls.append(ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=tc["function"]["arguments"],
            ))

        return LlmResponse(
            text=choice.get("content") or "",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            tool_calls=tool_calls,
            raw_message=choice,
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
