from aiogram import Router

from bot.presentation.handlers.giveaway.callbacks import router as callbacks_router
from bot.presentation.handlers.giveaway.create import router as create_router
from bot.presentation.handlers.giveaway.helpers import (
    _format_end_time,
    _format_prizes,
    _join_kb,
    _post_results,
)
from bot.presentation.handlers.giveaway.mute_roulette import (
    _finish_mute_roulette,
    router as mute_roulette_router,
)

router = Router(name="giveaway")
router.include_router(create_router)
router.include_router(callbacks_router)
router.include_router(mute_roulette_router)

__all__ = [
    "router",
    "_post_results",
    "_finish_mute_roulette",
    "_format_end_time",
    "_format_prizes",
    "_join_kb",
]
