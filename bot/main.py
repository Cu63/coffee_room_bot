import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka

from bot.infrastructure.config_loader import Settings, load_config
from bot.infrastructure.di import AppProvider, RequestProvider
from bot.presentation.handlers.reactions import router as reactions_router
from bot.presentation.handlers.commands import router as commands_router
from bot.presentation.handlers.blackjack import router as blackjack_router
from bot.presentation.handlers.slots import router as slots_router
from bot.presentation.handlers.llm_commands import router as llm_router
from bot.presentation.handlers.admin_commands import create_admin_router, _unmute_user

from bot.presentation.middlewares.chat_context import ChatContextMiddleware
from bot.presentation.middlewares.track_message import TrackMessageMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_HOURS = 6
UNMUTE_CHECK_INTERVAL_SECONDS = 60


async def cleanup_loop(container) -> None:
    """Фоновая задача: удаление устаревших событий."""
    from bot.application.cleanup_service import CleanupService

    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
        try:
            async with container() as request_scope:
                service = await request_scope.get(CleanupService)
                await service.delete_expired_events()
        except Exception:
            logger.exception("Cleanup task failed")


async def unmute_loop(container, bot: Bot) -> None:
    """Фоновая задача: каждую минуту проверяет истёкшие муты и восстанавливает права."""
    from bot.application.mute_service import MuteService

    while True:
        await asyncio.sleep(UNMUTE_CHECK_INTERVAL_SECONDS)
        try:
            async with container() as request_scope:
                mute_service = await request_scope.get(MuteService)
                expired = await mute_service.get_expired_mutes()
                for entry in expired:
                    logger.info(
                        "Unmuting user %d in chat %d (was_admin=%s)",
                        entry.user_id, entry.chat_id, entry.was_admin,
                    )
                    await _unmute_user(bot, mute_service, entry)
        except Exception:
            logger.exception("Unmute task failed")


async def main() -> None:
    settings = Settings()
    config = load_config()

    container = make_async_container(AppProvider(), RequestProvider())

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    # Outer: фильтр только групповых чатов (до dishka)
    dp.message.outer_middleware(ChatContextMiddleware())
    dp.message_reaction.outer_middleware(ChatContextMiddleware())
    dp.callback_query.outer_middleware(ChatContextMiddleware())

    dp.include_router(commands_router)
    dp.include_router(blackjack_router)
    dp.include_router(llm_router)
    dp.include_router(reactions_router)
    dp.include_router(slots_router)

    # Команды бота (без префикса)
    admin_router = create_admin_router(config.admin.prefix)
    dp.include_router(admin_router)
    logger.info(
        "Commands: /add, /sub, /set, /reset, /op, /mute, /selfmute, /tag, /save, /restore, /help, /limits"
    )

    # Dishka DI — регистрирует свой middleware
    setup_dishka(container, dp)

    # Outer: трекинг сообщений (ПОСЛЕ dishka, т.к. нужен контейнер в data)
    dp.message.outer_middleware(TrackMessageMiddleware())

    cleanup_task = asyncio.create_task(cleanup_loop(container))
    unmute_task = asyncio.create_task(unmute_loop(container, bot))

    logger.info("Bot starting…")
    try:
        DEBUG_ENV = os.getenv("SPECIAL__DEBUG_ENV")
        if DEBUG_ENV is not None and DEBUG_ENV == "TRUE":
            logging.critical("RUNNING WITH DEBUG ENV")
            await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        unmute_task.cancel()
        await container.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())