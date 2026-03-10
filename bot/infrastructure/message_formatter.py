from html import escape

from bot.domain.pluralizer import ScorePluralizer


def user_link(username: str | None, full_name: str, user_id: int) -> str:
    """Кликабельная ссылка на пользователя без тега (HTML parse mode).

    С username:  <a href="https://t.me/username">username</a>
    Без username: <a href="tg://user?id=123">Full Name</a>
    """
    if username:
        safe = escape(username)
        return f'<a href="https://t.me/{safe}">{safe}</a>'
    safe = escape(full_name) or str(user_id)
    return f'<a href="tg://user?id={user_id}">{safe}</a>'


class MessageFormatter:
    def __init__(self, templates: dict[str, str], pluralizer: ScorePluralizer) -> None:
        self._t = templates
        self._p = pluralizer

    def score_changed(self, user: str, delta: int, total: int) -> str:
        verb = "получает" if delta > 0 else "теряет"
        return self._t["score_changed"].format(
            user=user,
            verb=verb,
            delta=abs(delta),
            score_word=self._p.pluralize(abs(delta)),
            score_word_total=self._p.pluralize(total),
            total=total,
        )

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
        return f'{title}\n<blockquote expandable>{body}</blockquote>'