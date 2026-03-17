"""Загрузчик списка слов для команды /rword.

Слова читаются из configs/words_ru.txt при первом обращении и кешируются
в памяти на всё время жизни процесса.

Формат файла: по одному слову на строку, строки начинающиеся с # — комментарии.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

_WORDS_FILE = Path("configs/words_ru.txt")

# Кеш: None = ещё не загружены, [] = загружены но пусто
_words_cache: list[str] | None = None


def _load_words() -> list[str]:
    """Загрузить и нормализовать список слов из файла."""
    words: list[str] = []
    try:
        with open(_WORDS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Нормализуем: убираем пробелы, переводим в верхний регистр
                word = line.strip().upper()
                # Оставляем только слова из одних букв (кириллица)
                if word.isalpha():
                    words.append(word)
    except FileNotFoundError:
        logger.error("Файл словаря не найден: %s", _WORDS_FILE)
    except OSError as e:
        logger.error("Ошибка чтения словаря: %s", e)

    logger.info("Словарь загружен: %d слов из %s", len(words), _WORDS_FILE)
    return words


def get_words() -> list[str]:
    """Вернуть полный кешированный список слов (в верхнем регистре)."""
    global _words_cache
    if _words_cache is None:
        _words_cache = _load_words()
    return _words_cache


def pick_random_word(min_len: int, max_len: int) -> str | None:
    """Выбрать случайное слово из словаря с длиной в диапазоне [min_len, max_len].

    Возвращает слово в верхнем регистре или None если подходящих нет.
    """
    all_words = get_words()
    candidates = [w for w in all_words if min_len <= len(w) <= max_len]
    if not candidates:
        logger.warning(
            "Нет слов длиной %d–%d в словаре (всего слов: %d)",
            min_len, max_len, len(all_words),
        )
        return None
    return random.choice(candidates)