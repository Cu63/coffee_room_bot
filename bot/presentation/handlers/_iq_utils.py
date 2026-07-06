"""Общий помощник для начисления IQ за победы в играх (/ttt, /rword).

За победу даётся config.iq.win_reward IQ, но не больше config.iq.daily_win_cap
IQ в сутки на пользователя (лимит считается в Redis, per-chat, по дню МСК).
Терять IQ можно без ограничений — здесь только начисление.
"""

from __future__ import annotations

import logging

from bot.application.iq_service import IqService
from bot.domain.tz import now_msk
from bot.infrastructure.config_loader import IqConfig
from bot.infrastructure.redis_store import RedisStore

logger = logging.getLogger(__name__)


async def award_game_iq(
    store: RedisStore,
    iq_service: IqService,
    cfg: IqConfig,
    user_id: int,
    chat_id: int,
) -> int | None:
    """Начисляет IQ за победу с учётом дневного лимита.

    Возвращает новый IQ пользователя, либо None если IQ отключён / дневной
    лимит исчерпан / начислять нечего.
    """
    if not cfg.enabled or cfg.win_reward <= 0 or cfg.daily_win_cap <= 0:
        return None

    day = now_msk().strftime("%Y%m%d")
    new_total = await store.iq_win_incrby(user_id, chat_id, day, cfg.win_reward)

    # Сколько из этой победы реально помещается под дневной лимит
    if new_total > cfg.daily_win_cap:
        granted = cfg.win_reward - (new_total - cfg.daily_win_cap)
    else:
        granted = cfg.win_reward
    if granted <= 0:
        return None

    new_iq = await iq_service.add_iq(user_id, chat_id, granted)
    logger.debug(
        "iq: user %d in chat %d +%d IQ за победу (итого IQ %d, за день %d/%d)",
        user_id, chat_id, granted, new_iq, min(new_total, cfg.daily_win_cap), cfg.daily_win_cap,
    )
    return new_iq
