"""Центральная временная зона проекта: GMT+3 (Москва)."""

from datetime import timezone, timedelta

TZ_MSK = timezone(timedelta(hours=3), name="MSK")


def now_msk():
    """Текущее время в GMT+3."""
    from datetime import datetime
    return datetime.now(TZ_MSK)


def to_msk(dt):
    """Конвертирует aware-datetime в GMT+3."""
    if dt is None:
        return None
    return dt.astimezone(TZ_MSK)
