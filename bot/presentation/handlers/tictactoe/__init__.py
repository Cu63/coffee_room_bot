"""Хендлер /ttt — дуэльные исчезающие крестики-нолики.

Правила:
  1. /ttt <ставка> — создать игру, ставка списывается сразу
  2. Второй игрок принимает (ставка тоже списывается)
  3. Первый ход определяется случайно
  4. Каждый игрок может иметь максимум 3 фигуры на поле
  5. При постановке 4-й — самая старая исчезает
  6. Победа — 3 в ряд (стандартные комбинации)
  7. После 50 ходов без победителя — ничья, ставки возвращаются
"""

from aiogram import Router

from bot.presentation.handlers.tictactoe.callbacks import router as callbacks_router
from bot.presentation.handlers.tictactoe.start import router as start_router

router = Router(name="tictactoe")
router.include_router(start_router)
router.include_router(callbacks_router)
