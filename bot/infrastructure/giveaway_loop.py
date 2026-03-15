import asyncio
import logging
import time
from datetime import datetime, timedelta

from aiogram import Bot

from bot.domain.tz import TZ_MSK

logger = logging.getLogger(__name__)


async def giveaway_loop(bot: Bot, container) -> None:
    """Каждые 60 секунд проверяет и завершает просроченные розыгрыши."""
    from bot.application.giveaway_service import GiveawayService
    from bot.domain.pluralizer import ScorePluralizer
    from bot.presentation.handlers.giveaway import _post_results

    while True:
        await asyncio.sleep(60)
        try:
            async with container() as scope:
                service: GiveawayService = await scope.get(GiveawayService)
                pluralizer: ScorePluralizer = await scope.get(ScorePluralizer)
                results = await service.finish_expired(datetime.now(TZ_MSK))

            for result in results:
                await _post_results(
                    bot,
                    result.giveaway,
                    result.winners,
                    result.participants_count,
                    pluralizer,
                )
                logger.info(
                    "Auto-finished giveaway %d in chat %d, %d winners",
                    result.giveaway.id,
                    result.giveaway.chat_id,
                    len(result.winners),
                )
        except Exception:
            logger.exception("Error in giveaway_loop")


async def giveaway_period_loop(bot: Bot, container) -> None:
    """Каждые 60 секунд проверяет периодические розыгрыши и запускает новые раунды."""
    from bot.application.giveaway_service import GiveawayService
    from bot.domain.pluralizer import ScorePluralizer
    from bot.infrastructure.redis_store import RedisStore
    from bot.presentation.handlers.giveaway import _format_end_time, _format_prizes, _join_kb

    while True:
        await asyncio.sleep(60)
        try:
            async with container() as scope:
                store: RedisStore = await scope.get(RedisStore)
                service: GiveawayService = await scope.get(GiveawayService)
                pluralizer: ScorePluralizer = await scope.get(ScorePluralizer)

                now = time.time()
                entries = await store.giveaway_period_all()

                for data in entries:
                    if data.get("next_run", 0) > now:
                        continue

                    chat_id: int = data["chat_id"]
                    prizes: list[int] = data["prizes"]
                    period_seconds: int = data["period_seconds"]
                    gp_id: str = data["gp_id"]
                    round_dur_secs: int | None = data.get("round_duration_seconds")

                    ends_at: datetime | None = None
                    if round_dur_secs:
                        ends_at = datetime.now(TZ_MSK) + timedelta(seconds=round_dur_secs)

                    try:
                        giveaway = await service.create(
                            chat_id=chat_id,
                            created_by=data["created_by"],
                            prizes=prizes,
                            ends_at=ends_at,
                        )
                        text = (
                            "🔁 <b>Периодический розыгрыш!</b>\n\n"
                            f"{_format_prizes(prizes, pluralizer)}\n\n"
                            f"⏰ Завершение: <b>{_format_end_time(ends_at)}</b>\n"
                            f"🆔 ID: <code>{giveaway.id}</code>"
                        )
                        sent = await bot.send_message(
                            chat_id, text, parse_mode="HTML", reply_markup=_join_kb(giveaway.id, 0)
                        )
                        await service.set_message_id(giveaway.id, sent.message_id)
                        logger.info(
                            "Started periodic giveaway %d (gp_id=%s) in chat %d",
                            giveaway.id,
                            gp_id,
                            chat_id,
                        )
                    except Exception:
                        logger.exception("Failed to start periodic giveaway gp_id=%s in chat %d", gp_id, chat_id)

                    # Обновляем next_run независимо от успеха (чтобы не зациклиться)
                    await store.giveaway_period_update_next_run(chat_id, gp_id, now + period_seconds)

        except Exception:
            logger.exception("Error in giveaway_period_loop")