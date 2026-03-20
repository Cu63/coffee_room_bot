"""Пакет хендлеров анаграммы.

Экспортирует:
- ``router`` — основной роутер, включающий все под-роутеры.
- ``create_anagram_game`` — утилита для автоматического создания игр (loop).
"""

from __future__ import annotations

from aiogram import Router

from bot.presentation.handlers.anagram.start import router as start_router
from bot.presentation.handlers.anagram.play import router as play_router
from bot.presentation.handlers.anagram.create import create_anagram_game

router = Router(name="anagram")
router.include_router(start_router)
router.include_router(play_router)

__all__ = ["router", "create_anagram_game"]
