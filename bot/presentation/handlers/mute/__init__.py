"""Обработчики команд мута: /mute, /amute, /selfmute, /unmute, /aunmute."""

from aiogram import Router

from bot.presentation.handlers.mute.amute_cmd import router as amute_router
from bot.presentation.handlers.mute.aunmute_cmd import router as aunmute_router
from bot.presentation.handlers.mute.mute_cmd import router as mute_cmd_router
from bot.presentation.handlers.mute.selfmute_cmd import router as selfmute_router
from bot.presentation.handlers.mute.unmute_cmd import router as unmute_router

router = Router(name="mute")
router.include_router(mute_cmd_router)
router.include_router(amute_router)
router.include_router(selfmute_router)
router.include_router(aunmute_router)
router.include_router(unmute_router)
