"""ThinkService — случайная мысль участника чата (/think).

Мысль пишет LLM от первого лица: уровень мышления задаёт IQ участника, тему —
последние сообщения чата. Провайдер тот же, что у /analyze и /ff (OpenAiClient),
модель — своя из config.think.model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.application.interfaces.llm_repository import ILlmRepository
from bot.application.interfaces.message_repository import ChatMessage, IMessageRepository
from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.domain.bot_utils import is_admin_account
from bot.infrastructure.config_loader import ThinkConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)

COMMAND = "think"

# IQ хранится как uint8 (0…255, по умолчанию 89): (верхняя граница, не включая; уровень)
_IQ_TIERS = (
    (50, "very_low"),
    (80, "low"),
    (110, "average"),
    (140, "high"),
    (200, "genius"),
)


def iq_tier(iq: int) -> str:
    """Уровень мышления по IQ: ключ стиля think_style_<уровень> в messages.yaml."""
    for bound, tier in _IQ_TIERS:
        if iq < bound:
            return tier
    return "transcendent"


def _author(msg: ChatMessage) -> str:
    return f"@{msg.username}" if msg.username else msg.full_name


class ThinkRateLimitExceeded(Exception):
    def __init__(self, used: int, limit: int) -> None:
        super().__init__(f"think: {used}/{limit} за сегодня")
        self.used = used
        self.limit = limit


@dataclass(slots=True)
class Thought:
    text: str
    used_today: int  # с этой мыслью
    daily_limit: int  # 0 — без лимита
    is_admin: bool


class ThinkService:
    def __init__(
        self,
        client: OpenAiClient,
        message_repo: IMessageRepository,
        llm_repo: ILlmRepository,
        config: ThinkConfig,
        formatter: MessageFormatter,
        tx: ITransactionManager,
        admin_users: list[str],
    ) -> None:
        self._client = client
        self._messages = message_repo
        self._llm_repo = llm_repo
        self._config = config
        self._fmt = formatter
        self._tx = tx
        self._admin_users = {u.lower() for u in admin_users}

    async def think(self, chat_id: int, user_id: int, username: str | None, name: str, iq: int) -> Thought:
        """Мысль участника. Raises ThinkRateLimitExceeded, если дневной лимит исчерпан (админов не касается)."""
        limit = self._config.daily_limit_per_user
        is_admin = is_admin_account(user_id, username, self._admin_users)
        used = await self._llm_repo.count_today(user_id, (COMMAND,))
        if limit > 0 and not is_admin and used >= limit:
            raise ThinkRateLimitExceeded(used, limit)

        recent = await self._messages.get_recent_with_text(chat_id, self._config.messages_count)
        context = "\n".join(f"{_author(msg)}: {msg.text}" for msg in recent) or self._fmt._t["think_no_context"]
        user_prompt = self._fmt._t["think_user_prompt"].format(
            name=f"@{username}" if username else name,
            iq=iq,
            style=self._fmt._t[f"think_style_{iq_tier(iq)}"],
            context=context,
        )

        await self._tx.checkpoint()  # не держим соединение из пула, пока ждём LLM
        resp = await self._client.chat(
            [
                {"role": "system", "content": self._fmt._t["think_system_prompt"]},
                {"role": "user", "content": user_prompt},
            ],
            model=self._config.model,
            max_tokens=self._config.max_output_tokens,
            temperature=self._config.temperature,
        )
        logger.info(
            "think: chat=%d user=%d iq=%d messages=%d in=%d out=%d",
            chat_id,
            user_id,
            iq,
            len(recent),
            resp.input_tokens,
            resp.output_tokens,
        )

        # Запрос в журнале — это и есть счётчик дневного лимита
        await self._llm_repo.log_request(
            user_id=user_id,
            chat_id=chat_id,
            command=COMMAND,
            query=f"iq={iq} messages={len(recent)}",
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        return Thought(
            text=(resp.text or "").strip() or self._fmt._t["think_empty"],
            used_today=used + 1,
            daily_limit=limit,
            is_admin=is_admin,
        )
