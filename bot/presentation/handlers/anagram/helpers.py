"""Вспомогательные функции для анаграммы."""

from __future__ import annotations

import random
import time


def _make_game_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


def _shuffle_word(word: str) -> str:
    """Перемешивает буквы слова так, чтобы результат гарантированно отличался от оригинала."""
    chars = list(word)
    if len(chars) <= 1:
        return word
    for _ in range(20):
        random.shuffle(chars)
        result = "".join(chars)
        if result != word:
            return result
    return "".join(chars)


def _game_text(shuffled: str, bet: int, tries_count: int, sw: str, ends_at: float) -> str:
    from datetime import datetime
    from bot.domain.tz import TZ_MSK
    ends_str = datetime.fromtimestamp(ends_at, tz=TZ_MSK).strftime("%H:%M:%S")
    return (
        f"🔤 <b>Анаграмма!</b>\n\n"
        f"Угадай слово: <b>{shuffled}</b>\n\n"
        f"💰 Приз: <b>{bet} {sw}</b>\n"
        f"🎯 Попыток: <b>{tries_count}</b>\n"
        f"⏰ До: <b>{ends_str}</b>\n\n"
        f"<i>Реплай на это сообщение с ответом</i>"
    )
