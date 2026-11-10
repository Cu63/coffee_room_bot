"""Центральная временная зона проекта: GMT+3 (Москва)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import overload

TZ_MSK = timezone(timedelta(hours=3), name="MSK")


def now_msk() -> datetime:
    """Текущее время в GMT+3."""
    return datetime.now(TZ_MSK)


@overload
def to_msk(dt: datetime) -> datetime: ...
@overload
def to_msk(dt: None) -> None: ...
def to_msk(dt: datetime | None) -> datetime | None:
    """Конвертирует aware-datetime в GMT+3."""
    if dt is None:
        return None
    return dt.astimezone(TZ_MSK)
