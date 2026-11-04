"""Регистрация роутеров и middleware для Dispatcher."""

from __future__ import annotations

import logging
from collections.abc import Collection

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)
from aiogram.types import User as TgUser
from dishka import AsyncContainer

from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.entities import User
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.presentation.middlewares.auto_delete import AutoDeleteCommandMiddleware
from bot.presentation.middlewares.auto_react import AutoReactMiddleware
from bot.presentation.middlewares.burst_bonus import BurstBonusMiddleware
from bot.presentation.middlewares.chat_context import ChatContextMiddleware
from bot.presentation.middlewares.iq_penalty import IqPenaltyMiddleware
from bot.presentation.middlewares.mute_mention_notify import MuteMentionNotifyMiddleware
from bot.presentation.middlewares.owner_mute import OwnerMuteDeleteMiddleware
from bot.presentation.middlewares.reply_chain_bonus import ReplyChainMiddleware
from bot.presentation.middlewares.spark_bonus import SparkBonusMiddleware
from bot.presentation.middlewares.telegram_errors import TelegramErrorsMiddleware
from bot.presentation.middlewares.track_message import TrackMessageMiddleware
from bot.presentation.middlewares.unsound_guard import UnsoundGuardMiddleware

logger = logging.getLogger(__name__)


async def register_bot_user(container: AsyncContainer, bot_me: TgUser) -> None:
    """Заводит бота в users.

    Баланс бота в каждом чате — касса (покупки, комиссии, призы), а scores.user_id
    ссылается на users. Раньше бот попадал в users только после первой автореакции,
    и в новой БД первая же покупка падала на внешнем ключе.
    """
    async with container() as scope:
        user_repo = await scope.get(IUserRepository)
        await user_repo.upsert(User(id=bot_me.id, username=bot_me.username, full_name=bot_me.full_name, is_bot=True))


async def warn_about_username_admins(container: AsyncContainer, admins: Collection[str]) -> None:
    """Подсказывает id для админов из admin.users, заданных по username.

    username можно сменить, и освободившееся имя займёт кто-то другой — вместе с админкой бота.
    """
    usernames = [entry for entry in admins if not entry.isdigit()]
    if not usernames:
        return
    async with container() as scope:
        user_repo = await scope.get(IUserRepository)
        for username in usernames:
            user = await user_repo.get_by_username(username)
            hint = f"замените на его id: {user.id}" if user else "id появится в users, когда он напишет в чат"
            logger.warning("admin.users: @%s задан по username, который можно сменить и занять; %s", username, hint)


def register_middlewares(dp: Dispatcher, bot_me: TgUser) -> None:
    """Регистрирует все middleware в правильном порядке."""
    # Самый внешний: гасит ожидаемые ошибки Telegram и AbortUpdate.
    # Должен стоять снаружи dishka — к нему исключение приходит уже
    # после закрытия транзакции запроса.
    errors_mw = TelegramErrorsMiddleware()
    dp.message.outer_middleware(errors_mw)
    dp.callback_query.outer_middleware(errors_mw)
    dp.message_reaction.outer_middleware(errors_mw)

    dp.message.outer_middleware(ChatContextMiddleware())
    dp.message_reaction.outer_middleware(ChatContextMiddleware())
    dp.callback_query.outer_middleware(ChatContextMiddleware())
    dp.message.middleware(AutoDeleteCommandMiddleware())

    # TrackMessageMiddleware и OwnerMute регистрируются ПОСЛЕ dishka setup
    # (вызываются из main.py отдельно)


def register_post_dishka_middlewares(dp: Dispatcher, bot_me: TgUser) -> None:
    """Middleware, которые должны быть зарегистрированы после setup_dishka."""
    # Первым: если сообщение тегает участника с активным /unsound, оно
    # удаляется и дальше по цепочке (трекинг, бонусы, хендлеры) не идёт.
    dp.message.outer_middleware(UnsoundGuardMiddleware(bot_me=bot_me))
    dp.message.outer_middleware(TrackMessageMiddleware(bot_me=bot_me))
    dp.message.outer_middleware(AutoReactMiddleware(bot_me=bot_me))
    dp.message.outer_middleware(BurstBonusMiddleware())
    dp.message.outer_middleware(SparkBonusMiddleware())
    dp.message.outer_middleware(ReplyChainMiddleware())
    dp.message.outer_middleware(IqPenaltyMiddleware())
    dp.message.outer_middleware(MuteMentionNotifyMiddleware())
    dp.message.outer_middleware(OwnerMuteDeleteMiddleware())


def _include(dp: Dispatcher, router: Router) -> None:
    """Подключает роутер модуля к dp, отцепив его от прежнего Dispatcher.

    Роутеры хендлеров — синглтоны модулей, а aiogram не даёт подключить роутер ко второму родителю.
    В боте Dispatcher один, но тесты собирают свой на каждый тест — в любом порядке и в одном процессе.
    Публичного способа отцепить роутер у aiogram нет.
    """
    previous = router.parent_router
    if previous is not None and previous is not dp:
        previous.sub_routers.remove(router)
        router._parent_router = None
    dp.include_router(router)


def register_routers(dp: Dispatcher, config: AppConfig, settings: BotSettings | None = None) -> None:
    """Регистрирует все роутеры хендлеров.

    ``settings`` нужен для команд, которые зависят от ключей в .env:
    без ключа роутер просто не регистрируется (команда «отсутствует»).
    """
    settings = settings or BotSettings()
    from bot.presentation.handlers.admin_chats import router as admin_chats_router
    from bot.presentation.handlers.admin_score import router as admin_score_router
    from bot.presentation.handlers.admin_user import router as admin_user_router
    from bot.presentation.handlers.anagram import router as anagram_router
    from bot.presentation.handlers.analyze import router as analyze_router
    from bot.presentation.handlers.anon import router as anon_router
    from bot.presentation.handlers.blackjack import router as blackjack_router
    from bot.presentation.handlers.buyop import router as buyop_router
    from bot.presentation.handlers.chatmode import router as chatmode_router
    from bot.presentation.handlers.commands import router as commands_router
    from bot.presentation.handlers.daily import router as daily_router
    from bot.presentation.handlers.dice import router as dice_router
    from bot.presentation.handlers.donotbuy import router as donotbuy_router
    from bot.presentation.handlers.duel import router as duel_router
    from bot.presentation.handlers.fanfic import router as fanfic_router
    from bot.presentation.handlers.genai import router as genai_router
    from bot.presentation.handlers.giveaway import router as giveaway_router
    from bot.presentation.handlers.help import router as help_router
    from bot.presentation.handlers.idea import router as idea_router
    from bot.presentation.handlers.iq import router as iq_router
    from bot.presentation.handlers.llm_commands import router as llm_router
    from bot.presentation.handlers.lot import router as lot_router
    from bot.presentation.handlers.mute import router as mute_router
    from bot.presentation.handlers.news import router as news_router
    from bot.presentation.handlers.poker import router as poker_router
    from bot.presentation.handlers.reactions import router as reactions_router
    from bot.presentation.handlers.renew import router as renew_router
    from bot.presentation.handlers.rounds import answer_router, quiz_router, whosaid_router
    from bot.presentation.handlers.selfban import router as selfban_router
    from bot.presentation.handlers.sixtyseven import router as sixtyseven_router
    from bot.presentation.handlers.slots import router as slots_router
    from bot.presentation.handlers.summary_admin import router as summary_admin_router
    from bot.presentation.handlers.tag import router as tag_router
    from bot.presentation.handlers.think import router as think_router
    from bot.presentation.handlers.tictactoe import router as ttt_router
    from bot.presentation.handlers.tracker import router as tracker_router
    from bot.presentation.handlers.transfer import router as transfer_router
    from bot.presentation.handlers.unsound import router as unsound_router
    from bot.presentation.handlers.warn import router as warn_router
    from bot.presentation.handlers.wordgame import router as wordgame_router

    _include(dp, commands_router)
    _include(dp, sixtyseven_router)
    if config.blackjack.enabled:
        _include(dp, blackjack_router)
    if config.dice.enabled:
        _include(dp, dice_router)
    _include(dp, llm_router)
    _include(dp, analyze_router)
    _include(dp, reactions_router)
    if config.slots.enabled:
        _include(dp, slots_router)
    _include(dp, giveaway_router)
    _include(dp, mute_router)
    if config.warn.enabled:
        _include(dp, warn_router)
    _include(dp, tag_router)
    _include(dp, transfer_router)
    _include(dp, renew_router)
    _include(dp, wordgame_router)
    _include(dp, admin_chats_router)
    _include(dp, admin_score_router)
    _include(dp, admin_user_router)
    _include(dp, help_router)
    if config.tictactoe.enabled:
        _include(dp, ttt_router)
    _include(dp, duel_router)
    if config.anagram.enabled:
        _include(dp, anagram_router)
    if config.lot.enabled:
        _include(dp, lot_router)
    _include(dp, buyop_router)
    _include(dp, idea_router)
    if config.iq.enabled:
        _include(dp, iq_router)
    if config.poker.enabled:
        _include(dp, poker_router)
    if config.donotbuy.enabled:
        _include(dp, donotbuy_router)
    _include(dp, selfban_router)
    _include(dp, summary_admin_router)
    _include(dp, tracker_router)
    _include(dp, anon_router)
    _include(dp, daily_router)
    _include(dp, chatmode_router)
    _include(dp, news_router)
    # /genai работает только при заданном GIGACHAT_API_KEY
    if config.genai.enabled and settings.gigachat_api_key:
        _include(dp, genai_router)
    if config.fanfic.enabled:
        _include(dp, fanfic_router)
    if config.think.enabled:
        _include(dp, think_router)
    if config.quiz.enabled:
        _include(dp, quiz_router)
    if config.whosaid.enabled:
        _include(dp, whosaid_router)
    if config.quiz.enabled or config.whosaid.enabled:
        _include(dp, answer_router)
    if config.unsound.enabled:
        _include(dp, unsound_router)


def registered_commands(router: Router) -> set[str]:
    """Команды, на которые отвечают хендлеры роутера и всех вложенных роутеров."""
    commands: set[str] = set()
    for handler in router.message.handlers:
        for flt in handler.filters or ():
            if isinstance(flt.callback, Command):
                commands.update(name for name in flt.callback.commands if isinstance(name, str))
    for sub in router.sub_routers:
        commands |= registered_commands(sub)
    return commands


async def setup_bot_commands(bot: Bot, dp: Dispatcher, menu: dict) -> None:
    """Меню команд Telegram из help.yaml (bot_commands): участникам в группах, админам групп и в личке.

    Команды, на которые бот не отвечает (выключенная фича, опечатка), в меню не попадают.
    Без меню бот работает как прежде, поэтому ошибка Telegram только пишется в лог.
    """
    known = registered_commands(dp)

    def section(name: str) -> list[BotCommand]:
        items = menu.get(name) or {}
        unknown = [command for command in items if command not in known]
        if unknown:
            logger.warning("Меню команд (%s): бот не отвечает на %s — пропускаю", name, ", ".join(unknown))
        return [BotCommand(command=command, description=text) for command, text in items.items() if command in known]

    members = section("members")
    scopes = (
        (BotCommandScopeAllGroupChats(), members),
        (BotCommandScopeAllChatAdministrators(), members + section("admins")),
        (BotCommandScopeAllPrivateChats(), section("private")),
    )
    for scope, commands in scopes:
        try:
            await bot.set_my_commands(commands, scope=scope)
        except TelegramAPIError:
            logger.warning("Не удалось поставить меню команд (%s)", scope.type, exc_info=True)
