"""Фоновая задача: ежедневный прогрессивный налог на крупные балансы."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import TaxConfig

logger = logging.getLogger(__name__)


def _seconds_until(target_time: str) -> float:
    now = datetime.now(TZ_MSK)
    hh, mm = map(int, target_time.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _compute_tax(balance: int, cfg: TaxConfig) -> int:
    """Прогрессивный налог: каждая ступень облагает только свою «полосу» баланса."""
    brackets = sorted(cfg.brackets, key=lambda b: b.threshold)
    tax = 0
    for i, bracket in enumerate(brackets):
        if balance <= bracket.threshold:
            break
        upper = brackets[i + 1].threshold if i + 1 < len(brackets) else balance
        taxable = min(balance, upper) - bracket.threshold
        if taxable > 0:
            tax += math.ceil(taxable * bracket.percent / 100)
    return max(tax, 0)


async def _run_once(bot: Bot, container) -> None:
    from bot.application.interfaces.message_repository import IMessageRepository
    from bot.application.interfaces.score_repository import IScoreRepository
    from bot.application.interfaces.user_repository import IUserRepository
    from bot.infrastructure.config_loader import AppConfig
    from bot.infrastructure.message_formatter import MessageFormatter, user_link

    async with container() as scope:
        config: AppConfig = await scope.get(AppConfig)
        cfg = config.tax
        if not cfg.enabled:
            return
        message_repo: IMessageRepository = await scope.get(IMessageRepository)
        chat_ids = await message_repo.get_active_chats()

    if not chat_ids:
        return

    min_threshold = min(b.threshold for b in cfg.brackets)

    for chat_id in chat_ids:
        try:
            async with container() as scope:
                score_repo: IScoreRepository = await scope.get(IScoreRepository)
                user_repo: IUserRepository = await scope.get(IUserRepository)
                formatter: MessageFormatter = await scope.get(MessageFormatter)
                p = formatter._p

                rich_users = await score_repo.get_rich_users(chat_id, min_threshold)
                if not rich_users:
                    continue

                user_ids = [s.user_id for s in rich_users]
                users_map = await user_repo.get_by_ids(user_ids)

                lines: list[str] = []
                total_tax = 0
                for score in rich_users:
                    tax = _compute_tax(score.value, cfg)
                    if tax <= 0:
                        continue
                    await score_repo.add_delta(score.user_id, chat_id, -tax)
                    await score_repo.add_delta(bot.id, chat_id, tax)
                    new_balance = score.value - tax
                    total_tax += tax

                    user = users_map.get(score.user_id)
                    name = user_link(user.username, user.full_name, user.id) if user else str(score.user_id)
                    lines.append(
                        f"  {name}: −{tax} {p.pluralize(tax)}"
                        f" ({score.value} → {new_balance})"
                    )

                if not lines:
                    continue

            text = (
                f"🏛 <b>Ежедневный налог</b>\n\n"
                + "\n".join(lines)
                + f"\n\nВсего собрано: <b>{total_tax}</b> {p.pluralize(total_tax)}"
            )
            try:
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except (TelegramBadRequest, TelegramForbiddenError) as e:
                logger.warning("tax: telegram error in chat %d: %s", chat_id, e)

        except Exception:
            logger.exception("tax: failed for chat %d", chat_id)


async def tax_loop(bot: Bot, container) -> None:
    from bot.infrastructure.config_loader import AppConfig

    async with container() as scope:
        config: AppConfig = await scope.get(AppConfig)
        cfg = config.tax

    if not cfg.enabled:
        logger.info("tax: disabled, loop not started")
        return

    logger.info("tax: scheduled at %s MSK", cfg.time)

    while True:
        wait = _seconds_until(cfg.time)
        logger.debug("tax: sleeping %.0f seconds until %s", wait, cfg.time)
        await asyncio.sleep(wait)

        try:
            await _run_once(bot, container)
        except Exception:
            logger.exception("tax: unexpected error in _run_once")

        await asyncio.sleep(60)
