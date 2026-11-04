from html import escape

from bot.domain.pluralizer import ScorePluralizer


def user_link(username: str | None, full_name: str, user_id: int) -> str:
    """Отображение пользователя без кликабельных t.me-ссылок (чтобы не засорять историю).

    С username:  <code>@username</code>  — копируется, но не упоминает
    Без username: plain full_name (или user_id как fallback)
    """
    if username:
        safe = escape(username)
        return f"<code>@{safe}</code>"
    safe = escape(full_name) or str(user_id)
    return safe


class MessageFormatter:
    def __init__(self, templates: dict[str, str], pluralizer: ScorePluralizer) -> None:
        self._t = templates
        self._p = pluralizer

    def amount(self, n: int) -> str:
        """Сумма с валютой в нужной форме: «100 кирчиков»."""
        return f"{n} {self._p.pluralize(n)}"

    def score_info(self, user: str, total: int) -> str:
        if total == 0:
            return self._t["score_info_zero"].format(
                user=user,
                score_word=self._p._plural_many,
            )
        return self._t["score_info"].format(
            user=user,
            total=total,
            score_word=self._p.pluralize(total),
        )

    def leaderboard(self, rows: list[tuple[int, str, int]]) -> str:
        if not rows:
            return self._t["leaderboard_empty"]
        lines = [self._t["leaderboard_title"]]
        for rank, user, total in rows:
            lines.append(
                self._t["leaderboard_row"].format(
                    rank=rank,
                    user=user,
                    total=total,
                    score_word=self._p.pluralize(total),
                )
            )
        return "\n".join(lines)

    def history(self, events: list[dict], days: int) -> str:
        if not events:
            return self._t["history_empty"]
        title = self._t["history_title"].format(days=days)
        rows: list[str] = []
        for e in events:
            rows.append(self._t["history_row"].format(**e))
        body = "\n".join(rows)
        return f"{title}\n<blockquote expandable>{body}</blockquote>"
