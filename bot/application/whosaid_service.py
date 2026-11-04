"""WhoSaidService — «кто это сказал?»: цитата из чата и четыре кандидата в авторы."""

from __future__ import annotations

import html
import random
from datetime import datetime, timedelta

from bot.application.interfaces.message_repository import IMessageRepository
from bot.domain.entities import RoundQuestion, User
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import WhoSaidConfig

OPTIONS = 4
_MAX_NAME = 40  # текст кнопки


def _names(people: list[User]) -> list[str]:
    """Подписи кнопок: @username или имя; одинаковые различаются номером."""
    names = [f"@{p.username}" if p.username else p.full_name[:_MAX_NAME] or str(p.id) for p in people]
    seen: dict[str, int] = {}
    for i, name in enumerate(names):
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            names[i] = f"{name} ({seen[name]})"
    return names


class WhoSaidService:
    def __init__(self, message_repo: IMessageRepository, config: WhoSaidConfig) -> None:
        self._messages = message_repo
        self._cfg = config

    async def pick(self, chat_id: int) -> RoundQuestion | None:
        """Цитата и четыре кандидата в авторы. None — в чате мало подходящих сообщений или авторов."""
        since = datetime.now(TZ_MSK) - timedelta(days=self._cfg.days)
        quote = await self._messages.random_quote(chat_id, self._cfg.min_length, self._cfg.max_length, since)
        if quote is None:
            return None
        decoys = await self._messages.random_authors(chat_id, quote.user_id, since, OPTIONS - 1)
        if len(decoys) < OPTIONS - 1:
            return None
        author = User(id=quote.user_id, username=quote.username, full_name=quote.full_name)
        people = [author, *decoys]
        random.shuffle(people)
        return RoundQuestion(
            text=f"«{html.escape(quote.text)}»",
            options=tuple(_names(people)),
            correct=people.index(author),
            author_id=quote.user_id,
        )
