import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka

from bot.infrastructure.config_loader import Settings, load_config
from bot.infrastructure.di import AppProvider, RequestProvider
from bot.infrastructure.dice_loop import dice_loop
from bot.infrastructure.giveaway_loop import giveaway_loop
from bot.presentation.handlers._admin_utils import _unmute_user
from bot.presentation.handlers.admin_score import router as admin_score_router
from bot.presentation.handlers.admin_user import router as admin_user_router
from bot.presentation.handlers.blackjack import router as blackjack_router
from bot.presentation.handlers.commands import router as commands_router
from bot.presentation.handlers.dice import router as dice_router
from bot.presentation.handlers.giveaway import router as giveaway_router
from bot.presentation.handlers.help import router as help_router
from bot.presentation.handlers.llm_commands import router as llm_router
from bot.presentation.handlers.mute import router as mute_router
from bot.presentation.handlers.protect import router as protect_router
from bot.presentation.handlers.reactions import router as reactions_router
from bot.presentation.handlers.slots import router as slots_router
from bot.presentation.handlers.tag import router as tag_router
from bot.presentation.handlers.renew import router as renew_router
from bot.presentation.handlers.transfer import router as transfer_router
from bot.presentation.middlewares.auto_delete import AutoDeleteCommandMiddleware
from bot.presentation.middlewares.chat_context import ChatContextMiddleware
from bot.presentation.middlewares.track_message import TrackMessageMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
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
                # Обрабатываем ключи inline — без аккумуляции в список
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
    """Фоновая задача: возврат ставки по истёкшим играм блекджека."""
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
                    message_id = data.get("message_id", 0)
                    logger.info("BJ timeout: returning %d to user %d in chat %d", bet, user_id, chat_id)
                    await score_service.add_score(user_id, chat_id, bet, admin_id=user_id)
                    if message_id:
                        try:
                            await bot.edit_message_text(
                                "⏰ Время вышло! Ставка возвращена.",
                                chat_id=chat_id,
                                message_id=message_id,
                                reply_markup=None,
                            )
                        except Exception:
                            pass
        except Exception:
            logger.exception("BJ cleanup loop failed")


async def main() -> None:
    settings = Settings()
    config = load_config()

    container = make_async_container(AppProvider(), RequestProvider())

    bot = Bot(token=settings.bot_token)

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

    dp.message.outer_middleware(ChatContextMiddleware())
    dp.message_reaction.outer_middleware(ChatContextMiddleware())
    dp.callback_query.outer_middleware(ChatContextMiddleware())
    dp.message.middleware(AutoDeleteCommandMiddleware())

    dp.include_router(commands_router)
    dp.include_router(blackjack_router)
    dp.include_router(dice_router)
    dp.include_router(llm_router)
    dp.include_router(reactions_router)
    dp.include_router(slots_router)
    dp.include_router(giveaway_router)
    dp.include_router(mute_router)
    dp.include_router(tag_router)
    dp.include_router(transfer_router)
    dp.include_router(renew_router)
    dp.include_router(protect_router)
    dp.include_router(admin_score_router)
    dp.include_router(admin_user_router)
    dp.include_router(help_router)
    logger.info(
        "Commands: /add, /sub, /set, /reset, /op, /deop, /mute,"
        " /amute, /selfmute, /unmute, /tag, /transfer,"
        " /protect, /save, /restore, /help, /limits"
    )

    setup_dishka(container, dp)
    dp.message.outer_middleware(TrackMessageMiddleware())

    sys_cfg = config.system
    cleanup_task = asyncio.create_task(cleanup_loop(container, sys_cfg.cleanup_interval_hours))
    unmute_task = asyncio.create_task(unmute_loop(container, bot, sys_cfg.unmute_check_interval_seconds))
    giveaway_task = asyncio.create_task(giveaway_loop(bot, container))
    dice_task = asyncio.create_task(dice_loop(bot, container))
    mute_roulette_task = asyncio.create_task(mute_roulette_loop(container, bot))
    bj_cleanup_task = asyncio.create_task(bj_cleanup_loop(container, bot))

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
        dice_task.cancel()
        mute_roulette_task.cancel()
        bj_cleanup_task.cancel()
        if tg_log_handler:
            tg_log_handler.stop()
        await container.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
