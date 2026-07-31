"""FanficService — фанфики про участников чата (/ff).

Использует того же провайдера, что /analyze и /wir (OpenAiClient), но своей
моделью из config.fanfic.model — она заметно дешевле основной.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.application.interfaces.llm_repository import ILlmRepository
from bot.application.interfaces.message_repository import ChatMessage, IMessageRepository
from bot.domain.entities import User
from bot.infrastructure.config_loader import FanficConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _hero_name(user: User) -> str:
    return f"@{user.username}" if user.username else user.full_name


def _format_hero_block(user: User, messages: list[ChatMessage]) -> str:
    """Блок контекста по одному герою: его последние сообщения."""
    header = f"=== {_hero_name(user)} ({_escape(user.full_name)}) ==="
    if not messages:
        return f"{header}\n(сообщений в базе нет)"
    lines = [f"— {_escape(msg.text)}" for msg in messages]
    return header + "\n" + "\n".join(lines)


@dataclass(slots=True)
class FanficResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class FanficService:
    def __init__(
        self,
        client: OpenAiClient,
        message_repo: IMessageRepository,
        llm_repo: ILlmRepository,
        config: FanficConfig,
        formatter: MessageFormatter,
    ) -> None:
        self._client = client
        self._repo = message_repo
        self._llm_repo = llm_repo
        self._config = config
        self._fmt = formatter

    async def write(
        self,
        chat_id: int,
        user_id: int,
        heroes: list[User],
        prompt: str,
    ) -> FanficResult:
        """Пишет фанфик про ``heroes`` с учётом их последних сообщений."""
        blocks: list[str] = []
        for hero in heroes:
            messages = await self._repo.get_recent_with_text(
                chat_id, self._config.messages_per_user, [hero.id]
            )
            blocks.append(_format_hero_block(hero, messages))

        user_prompt = self._fmt._t["ff_user_prompt"].format(
            users=", ".join(_hero_name(h) for h in heroes),
            context="\n\n".join(blocks),
            prompt=_escape(prompt) if prompt else self._fmt._t["ff_no_plot"],
        )

        resp = await self._client.chat(
            [
                {"role": "system", "content": self._fmt._t["ff_system_prompt"]},
                {"role": "user", "content": user_prompt},
            ],
            model=self._config.model,
            max_tokens=self._config.max_output_tokens,
            temperature=self._config.temperature,
        )

        logger.info(
            "ff: chat=%d user=%d heroes=%d in=%d out=%d",
            chat_id, user_id, len(heroes), resp.input_tokens, resp.output_tokens,
        )

        try:
            await self._llm_repo.log_request(
                user_id=user_id,
                chat_id=chat_id,
                command="ff",
                query=f"heroes={len(heroes)} prompt={prompt[:100]}",
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
            )
        except Exception:
            logger.exception("ff: не удалось залогировать запрос")

        return FanficResult(
            text=resp.text or self._fmt._t["ff_empty"],
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
