"""Обработчики игры «Угадайка».

Флоу:
1. /word <ставка> <время>  — в группе: создаёт игру, пишет создателю в ЛС
2. /start в ЛС — предлагает ввести слово если есть pending
3. Текст в ЛС — создатель отправляет загаданное слово
4. Reply на игровое сообщение бота — попытка угадать слово
"""

from aiogram import Router

from bot.presentation.handlers.wordgame.guess import router as guess_router
from bot.presentation.handlers.wordgame.play import router as play_router
from bot.presentation.handlers.wordgame.start import router as start_router

router = Router(name="wordgame")
router.include_router(start_router)
router.include_router(play_router)
router.include_router(guess_router)

__all__ = ["router"]
