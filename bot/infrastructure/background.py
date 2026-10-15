"""Фоновые циклы под присмотром.

Раньше циклы запускались голым asyncio.create_task: если исключение вылетало мимо
внутреннего try (например, при получении зависимости до цикла), задача тихо умирала —
до остановки бота, а ошибка всплывала разве что в «Task exception was never retrieved».
Здесь упавший цикл пишет ошибку в лог и перезапускается после паузы.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

RESTART_DELAY = 30.0


class BackgroundTasks:
    def __init__(self, restart_delay: float = RESTART_DELAY) -> None:
        self._restart_delay = restart_delay
        self._tasks: list[asyncio.Task[None]] = []

    def start(self, name: str, loop: Callable[[], Awaitable[None]]) -> None:
        """Запустить цикл: loop() вызывается заново после каждого падения или выхода."""
        self._tasks.append(asyncio.create_task(self._supervise(name, loop), name=name))

    async def _supervise(self, name: str, loop: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await loop()
                logger.error("Фоновый цикл %s завершился сам — перезапуск через %.0f с", name, self._restart_delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Фоновый цикл %s упал — перезапуск через %.0f с", name, self._restart_delay)
            await asyncio.sleep(self._restart_delay)

    async def stop(self) -> None:
        """Остановить все циклы и дождаться их завершения."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
