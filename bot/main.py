import asyncio
import gc
import logging
import os

import asyncpg
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
from bot.infrastructure.logger import mask_url_credentials, setup_logger
from bot.infrastructure.telegram_retry import RetryIdempotentRequestsMiddleware
from bot.infrastructure.word_loader import get_words

logger = logging.getLogger(__name__)

# Все модули загружены — переводим их объекты в постоянное поколение.
# GC больше не будет сканировать ~50k+ объектов от aiogram/asyncio/pydantic
# при каждом цикле сборки мусора.
gc.freeze()


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
        logger.info("Using Telegram proxy: %s", mask_url_credentials(proxy))

    session = AiohttpSession(timeout=15, proxy=proxy)
    # Сетевые сбои повторяются точечно — только для идемпотентных запросов
    session.middleware(RetryIdempotentRequestsMiddleware())
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
        register_bot_user,
        register_middlewares,
        register_post_dishka_middlewares,
        register_routers,
        warn_about_username_admins,
    )

    await register_bot_user(container, bot_me)
    if not get_words():
        logger.warning("Словарь configs/words_ru.txt не найден или пуст: /rword и анаграммы не смогут подобрать слово")
    await warn_about_username_admins(container, config.admin.users)
    register_middlewares(dp, bot_me)
    register_routers(dp, config, settings)
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
                'changelog [tracker %d]: %d записей. Последнее: "%s" (%s)',
                tracker_chat_id,
                len(entries),
                title,
                date,
            )
    except Exception:
        logger.warning("changelog: не удалось прочитать при старте")

    # ── Фоновые задачи ───────────────────────────────────────────
    from bot.infrastructure.anagram_loop import anagram_auto_loop, anagram_expire_loop
    from bot.infrastructure.background import BackgroundTasks
    from bot.infrastructure.bj_cleanup_loop import bj_cleanup_loop
    from bot.infrastructure.chatmode_loop import chatmode_loop
    from bot.infrastructure.cleanup_loop import cleanup_loop
    from bot.infrastructure.daily_leaderboard_loop import daily_leaderboard_loop
    from bot.infrastructure.daily_summary_loop import daily_summary_loop
    from bot.infrastructure.dice_loop import dice_loop
    from bot.infrastructure.duel_cleanup_loop import duel_cleanup_loop
    from bot.infrastructure.giveaway_loop import giveaway_loop, giveaway_period_loop
    from bot.infrastructure.heartbeat import heartbeat_loop
    from bot.infrastructure.ledger_audit_loop import ledger_audit_loop
    from bot.infrastructure.lot_loop import lot_loop
    from bot.infrastructure.mute_roulette_loop import mute_roulette_loop
    from bot.infrastructure.tax_loop import tax_loop
    from bot.infrastructure.ttt_cleanup_loop import ttt_cleanup_loop
    from bot.infrastructure.unmute_loop import unmute_loop
    from bot.infrastructure.wordgame_loop import wordgame_loop
    from bot.presentation.utils import delete_loop

    sys_cfg = config.system
    tasks = BackgroundTasks()
    tasks.start("cleanup", lambda: cleanup_loop(container, sys_cfg.cleanup_interval_hours))
    tasks.start("unmute", lambda: unmute_loop(container, bot, sys_cfg.unmute_check_interval_seconds))
    tasks.start("giveaway", lambda: giveaway_loop(bot, container))
    tasks.start("giveaway_period", lambda: giveaway_period_loop(bot, container))
    tasks.start("dice", lambda: dice_loop(bot, container))
    tasks.start("mute_roulette", lambda: mute_roulette_loop(container, bot))
    tasks.start("bj_cleanup", lambda: bj_cleanup_loop(container, bot))
    tasks.start("duel_cleanup", lambda: duel_cleanup_loop(container, bot))
    tasks.start("ttt_cleanup", lambda: ttt_cleanup_loop(container, bot))
    tasks.start("lot", lambda: lot_loop(bot, container))
    tasks.start("anagram_expire", lambda: anagram_expire_loop(bot, container))
    tasks.start("anagram_auto", lambda: anagram_auto_loop(bot, container))
    tasks.start("wordgame", lambda: wordgame_loop(bot, container))
    tasks.start("delete", lambda: delete_loop(bot, redis))
    tasks.start("daily_summary", lambda: daily_summary_loop(bot, container))
    tasks.start("daily_leaderboard", lambda: daily_leaderboard_loop(bot, container))
    tasks.start("chatmode", lambda: chatmode_loop(bot, container))
    tasks.start("tax", lambda: tax_loop(bot, container))
    tasks.start("ledger_audit", lambda: ledger_audit_loop(container))
    db_pool = await container.get(asyncpg.Pool)
    tasks.start("heartbeat", lambda: heartbeat_loop(db_pool, redis))

    logger.info("Bot starting…")
    try:
        if os.getenv("SPECIAL__DEBUG_ENV") == "TRUE":
            logging.critical("RUNNING WITH DEBUG ENV")
            await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await tasks.stop()
        if tg_log_handler:
            tg_log_handler.stop()
        await container.close()
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
