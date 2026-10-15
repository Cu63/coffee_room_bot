"""Общие утилиты — используются в нескольких слоях.

Не импортирует aiogram (чистый домен).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Protocol


class Account(Protocol):
    """Пользователь Telegram: годится и aiogram.types.User, и domain.entities.User."""

    @property
    def id(self) -> int: ...

    @property
    def username(self) -> str | None: ...


def is_admin(user: Account | None, admins: Collection[str]) -> bool:
    """Админ бота из конфига (admin.users)."""
    return user is not None and is_admin_account(user.id, user.username, admins)


def is_admin_account(user_id: int, username: str | None, admins: Collection[str]) -> bool:
    """admins — нормализованный admin.users: числовые id строками и username в нижнем регистре без @.

    Надёжнее id: username можно сменить, и освободившееся имя займёт кто-то другой —
    вместе с админкой бота.
    """
    if str(user_id) in admins:
        return True
    return bool(username) and username.lower() in admins


_DURATION_PATTERN = re.compile(r"^(?:(\d+)[dд])?(?:(\d+)[hч])?(?:(\d+)[mм])?(?:(\d+)[sс])?$")


def parse_duration(arg: str) -> int | None:
    """Парсит длительность в секунды.

    Поддерживаемые форматы (можно комбинировать):
      1d1h10m  1d  2h  30m  45s  1h30m  1d12h
    Суффиксы: d/д (дни), h/ч (часы), m/м (минуты), s/с (секунды).
    Без суффикса — трактуется как минуты.
    Возвращает количество секунд или None при ошибке.
    """
    arg = arg.strip().lower()
    m = _DURATION_PATTERN.fullmatch(arg)
    if m and any(m.groups()):
        days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups())
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    # Голое число — минуты
    try:
        return int(arg) * 60
    except ValueError:
        return None


def format_duration(seconds: int) -> str:
    """Форматирует секунды: '1д 2ч 5м', '30м', '45с'."""
    parts = []
    if seconds >= 86400:
        parts.append(f"{seconds // 86400}д")
        seconds %= 86400
    if seconds >= 3600:
        parts.append(f"{seconds // 3600}ч")
        seconds %= 3600
    if seconds >= 60:
        parts.append(f"{seconds // 60}м")
        seconds %= 60
    if seconds > 0:
        parts.append(f"{seconds}с")
    return " ".join(parts) or "0с"
