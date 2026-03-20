"""Регистрация роутеров и middleware для Dispatcher."""

from __future__ import annotations

from aiogram import Dispatcher
from aiogram.types import User as TgUser

from bot.infrastructure.config_loader import AppConfig
from bot.presentation.middlewares.auto_delete import AutoDeleteCommandMiddleware
from bot.presentation.middlewares.auto_react import AutoReactMiddleware
from bot.presentation.middlewares.burst_bonus import BurstBonusMiddleware
from bot.presentation.middlewares.chat_context import ChatContextMiddleware
from bot.presentation.middlewares.mute_mention_notify import MuteMentionNotifyMiddleware
from bot.presentation.middlewares.owner_mute import OwnerMuteDeleteMiddleware
from bot.presentation.middlewares.reply_chain_bonus import ReplyChainMiddleware
from bot.presentation.middlewares.retry_network import RetryNetworkMiddleware
from bot.presentation.middlewares.spark_bonus import SparkBonusMiddleware
from bot.presentation.middlewares.track_message import TrackMessageMiddleware


def register_middlewares(dp: Dispatcher, bot_me: TgUser) -> None:
    """Регистрирует все middleware в правильном порядке."""
    retry_mw = RetryNetworkMiddleware()
    dp.message.outer_middleware(retry_mw)
    dp.callback_query.outer_middleware(retry_mw)
    dp.message_reaction.outer_middleware(retry_mw)

    dp.message.outer_middleware(ChatContextMiddleware())
    dp.message_reaction.outer_middleware(ChatContextMiddleware())
    dp.callback_query.outer_middleware(ChatContextMiddleware())
    dp.message.middleware(AutoDeleteCommandMiddleware())

    # TrackMessageMiddleware и OwnerMute регистрируются ПОСЛЕ dishka setup
    # (вызываются из main.py отдельно)


def register_post_dishka_middlewares(dp: Dispatcher, bot_me: TgUser) -> None:
    """Middleware, которые должны быть зарегистрированы после setup_dishka."""
    dp.message.outer_middleware(TrackMessageMiddleware(bot_me=bot_me))
    dp.message.outer_middleware(AutoReactMiddleware(bot_me=bot_me))
    dp.message.outer_middleware(BurstBonusMiddleware())
    dp.message.outer_middleware(SparkBonusMiddleware())
    dp.message.outer_middleware(ReplyChainMiddleware())
    dp.message.outer_middleware(MuteMentionNotifyMiddleware())
    dp.message.outer_middleware(OwnerMuteDeleteMiddleware())


def register_routers(dp: Dispatcher, config: AppConfig) -> None:
    """Регистрирует все роутеры хендлеров."""
    from bot.presentation.handlers.admin_score import router as admin_score_router
    from bot.presentation.handlers.admin_user import router as admin_user_router
    from bot.presentation.handlers.anagram import router as anagram_router
    from bot.presentation.handlers.anon import router as anon_router
    from bot.presentation.handlers.blackjack import router as blackjack_router
    from bot.presentation.handlers.buyop import router as buyop_router
    from bot.presentation.handlers.chatmode import router as chatmode_router
    from bot.presentation.handlers.commands import router as commands_router
    from bot.presentation.handlers.daily import router as daily_router
    from bot.presentation.handlers.dice import router as dice_router
    from bot.presentation.handlers.duel import router as duel_router
    from bot.presentation.handlers.giveaway import router as giveaway_router
    from bot.presentation.handlers.help import router as help_router
    from bot.presentation.handlers.idea import router as idea_router
    from bot.presentation.handlers.llm_commands import router as llm_router
    from bot.presentation.handlers.analyze import router as analyze_router
    from bot.presentation.handlers.lot import router as lot_router
    from bot.presentation.handlers.mute import router as mute_router
    from bot.presentation.handlers.protect import router as protect_router
    from bot.presentation.handlers.reactions import router as reactions_router
    from bot.presentation.handlers.renew import router as renew_router
    from bot.presentation.handlers.selfban import router as selfban_router
    from bot.presentation.handlers.slots import router as slots_router
    from bot.presentation.handlers.tag import router as tag_router
    from bot.presentation.handlers.tictactoe import router as ttt_router
    from bot.presentation.handlers.tracker import router as tracker_router
    from bot.presentation.handlers.transfer import router as transfer_router
    from bot.presentation.handlers.wordgame import router as wordgame_router

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
    dp.include_router(duel_router)
    if config.anagram.enabled:
        dp.include_router(anagram_router)
    if config.lot.enabled:
        dp.include_router(lot_router)
    dp.include_router(buyop_router)
    dp.include_router(idea_router)
    dp.include_router(selfban_router)
    dp.include_router(tracker_router)
    dp.include_router(anon_router)
    dp.include_router(daily_router)
    dp.include_router(chatmode_router)
