import asyncio
import logging
import os

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka

from bot.infrastructure.config_loader import BotSettings, load_config
from bot.infrastructure.di import AppProvider, RequestProvider
from bot.infrastructure.dice_loop import dice_loop
from bot.infrastructure.giveaway_loop import giveaway_loop, giveaway_period_loop
from bot.infrastructure.wordgame_loop import wordgame_loop
from bot.infrastructure.daily_summary_loop import daily_summary_loop
from bot.infrastructure.daily_leaderboard_loop import daily_leaderboard_loop
from bot.infrastructure.logger import setup_logger
from bot.presentation.handlers._admin_utils import _unmute_user
from bot.presentation.handlers.admin_score import router as admin_score_router
from bot.presentation.handlers.admin_user import router as admin_user_router
from bot.presentation.handlers.blackjack import router as blackjack_router
from bot.presentation.handlers.tracker import router as tracker_router
from bot.presentation.handlers.anon import router as anon_router
from bot.presentation.handlers.daily import router as daily_router
from bot.presentation.handlers.commands import router as commands_router
from bot.presentation.handlers.dice import router as dice_router
from bot.presentation.handlers.giveaway import router as giveaway_router
from bot.presentation.handlers.help import router as help_router
from bot.presentation.handlers.llm_commands import router as llm_router
from bot.presentation.handlers.analyze import router as analyze_router
from bot.presentation.handlers.mute import router as mute_router
from bot.presentation.handlers.protect import router as protect_router
from bot.presentation.handlers.reactions import router as reactions_router
from bot.presentation.handlers.slots import router as slots_router
from bot.presentation.handlers.tag import router as tag_router
from bot.presentation.handlers.renew import router as renew_router
from bot.presentation.handlers.wordgame import router as wordgame_router
from bot.presentation.handlers.transfer import router as transfer_router
from bot.presentation.handlers.tictactoe import router as ttt_router
from bot.presentation.handlers.buyop import router as buyop_router
from bot.presentation.handlers.idea import router as idea_router
from bot.presentation.middlewares.auto_delete import AutoDeleteCommandMiddleware
from bot.presentation.middlewares.chat_context import ChatContextMiddleware
from bot.presentation.middlewares.owner_mute import OwnerMuteDeleteMiddleware
from bot.presentation.middlewares.retry_network import RetryNetworkMiddleware
from bot.presentation.middlewares.track_message import TrackMessageMiddleware
from bot.presentation import utils as presentation_utils
from bot.presentation.utils import delete_loop

# Логгер инициализируется после load_config() в main() — здесь только получаем инстанс.
# setup_logger() вызывается первым делом в main() и настраивает root logger,
# поэтому все последующие вызовы getLogger(__name__) в любом модуле будут через structlog.
logger = logging.getLogger(__name__)


async def cleanup_loop(container, interval_hours: int) -> None:
    """Фоновая задача: удаление устаревших событий."""
    from bot.application.cleanup_service import CleanupService

    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            async with container() as scope:
                service = await scope.get(CleanupService)
                await service.delete_expired_events()
        except Exception:
            logger.exception("Cleanup task failed")


async def unmute_loop(container, bot: Bot, interval_seconds: int) -> None:
    """Фоновая задача: проверяет истёкшие муты и восстанавливает права."""
    from bot.application.mute_service import MuteService

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with container() as scope:
                mute_service = await scope.get(MuteService)
                expired = await mute_service.get_expired_mutes()
                for entry in expired:
                    logger.info(
                        "Unmuting user %d in chat %d (was_admin=%s)", entry.user_id, entry.chat_id, entry.was_admin
                    )
                    await _unmute_user(bot, mute_service, entry)
        except Exception:
            logger.exception("Unmute task failed")


async def mute_roulette_loop(container, bot: Bot) -> None:
    """Фоновая задача: завершает истёкшие мут-рулетки."""
    import json
    import time as _time

    from bot.application.mute_service import MuteService
    from bot.infrastructure.redis_store import RedisStore
    from bot.presentation.handlers.giveaway import _finish_mute_roulette

    while True:
        await asyncio.sleep(10)
        try:
            async with container() as scope:
                store = await scope.get(RedisStore)
                mute_service = await scope.get(MuteService)
                now = _time.time()
                async for key in store._r.scan_iter("mutegiveaway:*"):
                    raw = await store._r.get(key)
                    if raw is None:
                        continue
                    data = json.loads(raw)
                    if data["ends_at"] <= now:
                        parts = key.split(":")
                        chat_id = int(parts[1])
                        roulette_id = parts[2]
                        finished = await store.mute_roulette_delete(chat_id, roulette_id)
                        if finished:
                            logger.info("Auto-finishing mutegiveaway %s in chat %d", roulette_id, chat_id)
                            await _finish_mute_roulette(bot, chat_id, finished, mute_service)
        except Exception:
            logger.exception("Mute roulette loop failed")


async def bj_cleanup_loop(container, bot: Bot) -> None:
    """Фоновая задача: закрывает истёкшие игры блекджека (возвращает половину ставки)."""
    from bot.application.score_service import ScoreService
    from bot.infrastructure.redis_store import RedisStore

    while True:
        await asyncio.sleep(15)
        try:
            async with container() as scope:
                store = await scope.get(RedisStore)
                score_service = await scope.get(ScoreService)
                for data in await store.bj_pop_expired():
                    user_id = data["player_id"]
                    chat_id = data["chat_id"]
                    bet = data["bet"]
                    refund = bet // 2
                    message_id = data.get("message_id", 0)
                    logger.info(
                        "BJ timeout: refunding %d/%d to user %d in chat %d",
                        refund, bet, user_id, chat_id,
                    )
                    if refund > 0:
                        await score_service.add_score(user_id, chat_id, refund, admin_id=user_id)
                    if message_id:
                        try:
                            await bot.edit_message_text(
                                f"⏰ Время вышло! Возвращено {refund} из {bet}.",
                                chat_id=chat_id,
                                message_id=message_id,
                                reply_markup=None,
                            )
                        except Exception:
                            pass
        except Exception:
            logger.exception("BJ cleanup loop failed")


async def main() -> None:
    config = load_config()

    # ── Логирование ──────────────────────────────────────────────
    # Инициализируем structlog до любых других действий,
    # чтобы все последующие logger.info/error шли через него.
    setup_logger(config.logging)

    settings = BotSettings()

    # ── Redis ────────────────────────────────────────────────────
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    presentation_utils.init_redis(redis)

    container = make_async_container(AppProvider(), RequestProvider())

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

    dp = Dispatcher()

    retry_mw = RetryNetworkMiddleware()
    dp.message.outer_middleware(retry_mw)
    dp.callback_query.outer_middleware(retry_mw)
    dp.message_reaction.outer_middleware(retry_mw)

    dp.message.outer_middleware(ChatContextMiddleware())
    dp.message_reaction.outer_middleware(ChatContextMiddleware())
    dp.callback_query.outer_middleware(ChatContextMiddleware())
    dp.message.middleware(AutoDeleteCommandMiddleware())

    dp.include_router(commands_router)
    if config.blackjack.enabled:
        dp.include_router(blackjack_router)
    if config.dice.enabled:
        dp.include_router(dice_router)
    dp.include_router(llm_router)
    dp.include_router(analyze_router)
    dp.include_router(reactions_router)
    if config.slots.enabled:
        dp.include_router(slots_router)
    dp.include_router(giveaway_router)
    dp.include_router(mute_router)
    dp.include_router(tag_router)
    dp.include_router(transfer_router)
    dp.include_router(renew_router)
    dp.include_router(wordgame_router)
    dp.include_router(protect_router)
    dp.include_router(admin_score_router)
    dp.include_router(admin_user_router)
    dp.include_router(help_router)
    if config.tictactoe.enabled:
        dp.include_router(ttt_router)
    dp.include_router(buyop_router)
    dp.include_router(idea_router)
    dp.include_router(tracker_router)
    dp.include_router(anon_router)
    dp.include_router(daily_router)

    setup_dishka(container, dp)
    dp.message.outer_middleware(TrackMessageMiddleware(bot_me=bot_me))
    dp.message.outer_middleware(OwnerMuteDeleteMiddleware())

    # ── Стартап-лог ченджлога ────────────────────────────────────
    try:
        all_logs = await redis.changelog_scan_all() if hasattr(redis, "changelog_scan_all") else []
    except Exception:
        all_logs = []
    # redis здесь — aioredis.Redis, changelog методы на RedisStore
    # создаём временный store только для лога
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

    sys_cfg = config.system
    cleanup_task = asyncio.create_task(cleanup_loop(container, sys_cfg.cleanup_interval_hours))
    unmute_task = asyncio.create_task(unmute_loop(container, bot, sys_cfg.unmute_check_interval_seconds))
    giveaway_task = asyncio.create_task(giveaway_loop(bot, container))
    giveaway_period_task = asyncio.create_task(giveaway_period_loop(bot, container))
    dice_task = asyncio.create_task(dice_loop(bot, container))
    mute_roulette_task = asyncio.create_task(mute_roulette_loop(container, bot))
    bj_cleanup_task = asyncio.create_task(bj_cleanup_loop(container, bot))
    wordgame_task = asyncio.create_task(wordgame_loop(bot, container))
    delete_task = asyncio.create_task(delete_loop(bot, redis))
    daily_summary_task = asyncio.create_task(daily_summary_loop(bot, container))
    daily_leaderboard_task = asyncio.create_task(daily_leaderboard_loop(bot, container))

    logger.info("Bot starting…")
    try:
        if os.getenv("SPECIAL__DEBUG_ENV") == "TRUE":
            logging.critical("RUNNING WITH DEBUG ENV")
            await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        unmute_task.cancel()
        giveaway_task.cancel()
        giveaway_period_task.cancel()
        dice_task.cancel()
        mute_roulette_task.cancel()
        bj_cleanup_task.cancel()
        wordgame_task.cancel()
        daily_summary_task.cancel()
        daily_leaderboard_task.cancel()
        delete_task.cancel()
        if tg_log_handler:
            tg_log_handler.stop()
        await container.close()
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())