"""LLM: не больше одного запроса к LLM на пользователя одновременно."""

from __future__ import annotations

import secrets

_LLM_BUSY = "llm:busy:"

# Отпускаем лок, только если он ещё наш: после истечения TTL его мог занять новый запрос
_RELEASE_IF_OWNER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class LlmStoreMixin:
    async def llm_busy_acquire(self, user_id: int, ttl: int) -> str | None:
        """Занять LLM-слот пользователя. Возвращает токен владельца или None, если слот занят."""
        token = secrets.token_hex(8)
        if await self._r.set(f"{_LLM_BUSY}{user_id}", token, nx=True, ex=ttl):
            return token
        return None

    async def llm_busy_release(self, user_id: int, token: str) -> None:
        await self._r.eval(_RELEASE_IF_OWNER, 1, f"{_LLM_BUSY}{user_id}", token)
