"""Хендлер /duel — вызов конкретного игрока на дуэль в /ttt или /bj.

Использование:
    /duel @username ttt <ставка>
    /duel @username bj <ставка>

Поведение:
  1. Создатель вводит команду → ставка списывается, отправляется сообщение с двумя кнопками.
  2. Целевой игрок видит сообщение с кнопками «Принять» (зелёная) и «Отказаться» (красная).
  3. Таймаут 2 минуты: если никто не нажал → сообщение удаляется, ставка возвращается.
  4. При принятии: ставка списывается с целевого, игра стартует немедленно.
  5. При отказе: ставка возвращается создателю, сообщение удаляется.
"""

from aiogram import Router

from bot.presentation.handlers.duel.start import router as start_router
from bot.presentation.handlers.duel.callbacks import router as callbacks_router

router = Router(name="duel")
router.include_router(start_router)
router.include_router(callbacks_router)
