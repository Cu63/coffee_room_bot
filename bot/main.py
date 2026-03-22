import asyncio
import logging
import os

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka

from bot.infrastructure.config_loader import BotSettings, load_config
from bot.infrastructure.di import (
    AppServiceProvider,
    DatabaseProvider,
    LlmProvider,
    PresentationProvider,
    RedisProvider,
)
from bot.infrastructure.logger import setup_logger

logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()

    # ── Логирование ──────────────────────────────────────────────
    setup_logger(config.logging)

    settings = BotSettings()

    # ── Redis ────────────────────────────────────────────────────
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    from bot.presentation import utils as presentation_utils
    presentation_utils.init_redis(redis)

    container = make_async_container(
        PresentationProvider(),
        DatabaseProvider(),
        RedisProvider(),
        LlmProvider(),
        AppServiceProvider(),
        context={aioredis.Redis: redis},
    )

    proxy = os.getenv("TELEGRAM_PROXY") or None
    if proxy:
        logger.info("Using Telegram proxy: %s", proxy)

    session = AiohttpSession(timeout=15, proxy=proxy)
    bot = Bot(token=settings.bot_token, session=session)

    bot_me = await bot.get_me()
    logger.info("Bot identity cached: @%s (id=%d)", bot_me.username, bot_me.id)

    # Мониторинг: отправка логов в Telegram-чат
    tg_log_handler = None
    if settings.log_chat_id:
        from bot.infrastructure.telegram_log_handler import TelegramLogHandler

        log_level = getattr(logging, settings.log_level.upper(), logging.ERROR)
        tg_log_handler = TelegramLogHandler(bot, settings.log_chat_id, level=log_level)
        tg_log_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(tg_log_handler)
        tg_log_handler.start()
        logger.info("Telegram log handler enabled (chat_id=%d, level=%s)", settings.log_chat_id, settings.log_level)

    # ── Dispatcher + middleware + routers ──────────────────────────
    dp = Dispatcher()

    from bot.infrastructure.bootstrap import (
        register_middlewares,
        register_post_dishka_middlewares,
        register_routers,
    )

    register_middlewares(dp, bot_me)
    register_routers(dp, config)
    setup_dishka(container, dp)
    register_post_dishka_middlewares(dp, bot_me)

    # ── Стартап-лог ченджлога ────────────────────────────────────
    from bot.infrastructure.redis_store import RedisStore as _RS
    _tmp_store = _RS(redis)
    try:
        all_logs = await _tmp_store.changelog_scan_all()
        for tracker_chat_id, entries in all_logs:
            latest = entries[0]
            title = latest.get("text", "").split("\n", 1)[0]
            date = latest.get("date", "?")
            logger.info(
                "changelog [tracker %d]: %d записей. Последнее: \"%s\" (%s)",
                tracker_chat_id, len(entries), title, date,
            )
    except Exception:
        logger.warning("changelog: не удалось прочитать при старте")

    # ── Фоновые задачи ───────────────────────────────────────────
    from bot.infrastructure.anagram_loop import anagram_auto_loop, anagram_expire_loop
    from bot.infrastructure.bj_cleanup_loop import bj_cleanup_loop
    from bot.infrastructure.chatmode_loop import chatmode_loop
    from bot.infrastructure.cleanup_loop import cleanup_loop
    from bot.infrastructure.daily_leaderboard_loop import daily_leaderboard_loop
    from bot.infrastructure.daily_summary_loop import daily_summary_loop
    from bot.infrastructure.dice_loop import dice_loop
    from bot.infrastructure.duel_cleanup_loop import duel_cleanup_loop
    from bot.infrastructure.giveaway_loop import giveaway_loop, giveaway_period_loop
    from bot.infrastructure.lot_loop import lot_loop
    from bot.infrastructure.mute_roulette_loop import mute_roulette_loop
    from bot.infrastructure.unmute_loop import unmute_loop
    from bot.infrastructure.wordgame_loop import wordgame_loop
    from bot.presentation.utils import delete_loop

    sys_cfg = config.system
    tasks = [
        asyncio.create_task(cleanup_loop(container, sys_cfg.cleanup_interval_hours)),
        asyncio.create_task(unmute_loop(container, bot, sys_cfg.unmute_check_interval_seconds)),
        asyncio.create_task(giveaway_loop(bot, container)),
        asyncio.create_task(giveaway_period_loop(bot, container)),
        asyncio.create_task(dice_loop(bot, container)),
        asyncio.create_task(mute_roulette_loop(container, bot)),
        asyncio.create_task(bj_cleanup_loop(container, bot)),
        asyncio.create_task(duel_cleanup_loop(container, bot)),
        asyncio.create_task(lot_loop(bot, container)),
        asyncio.create_task(anagram_expire_loop(bot, container)),
        asyncio.create_task(anagram_auto_loop(bot, container)),
        asyncio.create_task(wordgame_loop(bot, container)),
        asyncio.create_task(delete_loop(bot, redis)),
        asyncio.create_task(daily_summary_loop(bot, container)),
        asyncio.create_task(daily_leaderboard_loop(bot, container)),
        asyncio.create_task(chatmode_loop(bot, container)),
    ]

    logger.info("Bot starting…")
    try:
        if os.getenv("SPECIAL__DEBUG_ENV") == "TRUE":
            logging.critical("RUNNING WITH DEBUG ENV")
            await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in tasks:
            task.cancel()
        if tg_log_handler:
            tg_log_handler.stop()
        await container.close()
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
