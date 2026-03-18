from __future__ import annotations

import logging
from datetime import date

from bot.application.interfaces.daily_leaderboard_repository import (
    DailyLeader,
    DailyLeaderboard,
    IDailyLeaderboardRepository,
)
from bot.application.score_service import ScoreService, SPECIAL_EMOJI
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.message_formatter import user_link

logger = logging.getLogger(__name__)

# Эмодзи для каждой категории
_CATEGORY_EMOJI = {
    "messages": "💬",
    "reactions_given": "👍",
    "reactions_received": "🌟",
    "replies": "↩️",
    "ttt_wins": "❌",
    "wordgame_wins": "🔤",
}

_CATEGORY_LABEL = {
    "messages": "Больше всех написал",
    "reactions_given": "Больше всех реагировал",
    "reactions_received": "Больше всех реакций собрал",
    "replies": "Больше всех реплаил",
    "ttt_wins": "Лучший в крестики-нолики",
    "wordgame_wins": "Лучший в угадайке",
}

_VALUE_LABEL = {
    "messages": ("сообщение", "сообщения", "сообщений"),
    "reactions_given": ("реакция", "реакции", "реакций"),
    "reactions_received": ("реакция", "реакции", "реакций"),
    "replies": ("реплай", "реплая", "реплаев"),
    "ttt_wins": ("победа", "победы", "побед"),
    "wordgame_wins": ("победа", "победы", "побед"),
}


def _pluralize_value(n: int, forms: tuple[str, str, str]) -> str:
    """Простой плюрализатор для числительных."""
    if 11 <= n % 100 <= 14:
        return forms[2]
    r = n % 10
    if r == 1:
        return forms[0]
    if 2 <= r <= 4:
        return forms[1]
    return forms[2]


def _format_leader(leader: DailyLeader, category: str) -> str:
    name = user_link(leader.username, leader.full_name, leader.user_id)
    word = _pluralize_value(leader.value, _VALUE_LABEL[category])
    emoji = _CATEGORY_EMOJI[category]
    label = _CATEGORY_LABEL[category]
    return f"{emoji} <b>{label}:</b> {name} — {leader.value} {word}"


class DailyLeaderboardService:
    def __init__(
        self,
        repo: IDailyLeaderboardRepository,
        score_service: ScoreService,
        pluralizer: ScorePluralizer,
    ) -> None:
        self._repo = repo
        self._score_service = score_service
        self._pluralizer = pluralizer

    async def get_leaderboard(self, chat_id: int, for_date: date) -> DailyLeaderboard:
        return await self._repo.get_daily_leaderboard(chat_id, for_date)

    async def award_and_format(
        self,
        chat_id: int,
        lb: DailyLeaderboard,
        bonuses: dict[str, int],
    ) -> str:
        """Начисляет бонусы лидерам и возвращает текст сообщения."""
        date_str = lb.date.strftime("%d.%m.%Y")
        lines = [f"🏆 <b>Итоги дня — {date_str}</b>\n"]

        categories = [
            ("messages", lb.top_messages),
            ("reactions_given", lb.top_reactions_given),
            ("reactions_received", lb.top_reactions_received),
            ("replies", lb.top_replies),
            ("ttt_wins", lb.top_ttt_wins),
            ("wordgame_wins", lb.top_wordgame_wins),
        ]

        for key, leader in categories:
            if leader is None:
                continue
            bonus = bonuses.get(key, 0)
            line = _format_leader(leader, key)
            if bonus > 0:
                score_word = self._pluralizer.pluralize(bonus)
                line += f" (+{bonus} {score_word})"
                try:
                    await self._score_service.award_daily_leader(
                        leader.user_id, chat_id, bonus
                    )
                except Exception:
                    logger.exception(
                        "daily_leaderboard: failed to award user %d in chat %d",
                        leader.user_id, chat_id,
                    )
            lines.append(line)

        if len(lines) == 1:
            return f"🏆 <b>Итоги дня — {date_str}</b>\n\n<i>Активности не было.</i>"

        return "\n".join(lines)

    def format_preview(
        self,
        today: DailyLeaderboard,
        yesterday: DailyLeaderboard | None,
    ) -> str:
        """Формирует текст для /daily — текущие лидеры + итоги вчера."""
        parts: list[str] = []

        # ── Вчера ─────────────────────────────────────────────────────────────
        if yesterday is not None and not yesterday.is_empty():
            date_str = yesterday.date.strftime("%d.%m.%Y")
            parts.append(f"📅 <b>Итоги вчера ({date_str}):</b>")
            parts.extend(_leaderboard_lines(yesterday, with_value=True))
            parts.append("")

        # ── Сегодня ───────────────────────────────────────────────────────────
        today_str = today.date.strftime("%d.%m.%Y")
        parts.append(f"📊 <b>Лидеры сегодня ({today_str}):</b>")
        if today.is_empty():
            parts.append("<i>Данных пока нет.</i>")
        else:
            parts.extend(_leaderboard_lines(today, with_value=True))

        return "\n".join(parts)


def _leaderboard_lines(lb: DailyLeaderboard, *, with_value: bool) -> list[str]:
    categories = [
        ("messages", lb.top_messages),
        ("reactions_given", lb.top_reactions_given),
        ("reactions_received", lb.top_reactions_received),
        ("replies", lb.top_replies),
        ("ttt_wins", lb.top_ttt_wins),
        ("wordgame_wins", lb.top_wordgame_wins),
    ]
    lines = []
    for key, leader in categories:
        if leader is None:
            continue
        lines.append(_format_leader(leader, key))
    return lines
