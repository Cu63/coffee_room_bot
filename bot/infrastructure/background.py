"""Фоновые циклы под присмотром.

Раньше циклы запускались голым asyncio.create_task: если исключение вылетало мимо
внутреннего try (например, при получении зависимости до цикла), задача тихо умирала —
до остановки бота, а ошибка всплывала разве что в «Task exception was never retrieved».
Здесь упавший цикл пишет ошибку в лог и перезапускается после паузы.

LoopRegistry помнит про каждый цикл последний удачный проход и последнюю ошибку — их
показывает /status. Проход цикл отмечает сам (loop_pass), а ошибку просто пишет в лог,
как и раньше: обработчик логов узнаёт цикл по contextvar, который ставит надзиратель.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)

RESTART_DELAY = 30.0
_ERROR_TEXT_LIMIT = 300


@dataclass(slots=True)
class LoopState:
    """Что известно о фоновом цикле (время — time.time())."""

    started_at: float  # последний запуск: после падения цикл запускается заново
    restarts: int = 0
    last_pass: float | None = None  # конец последнего удачного прохода
    last_error_at: float | None = None
    last_error: str | None = None


class LoopRegistry:
    """Состояние фоновых циклов процесса по их именам."""

    def __init__(self) -> None:
        self.started_at = time.time()  # старт бота: аптайм для /status
        self.loops: dict[str, LoopState] = {}


# Цикл, внутри которого выполняется код: надзиратель ставит его в контексте своей задачи
_current: ContextVar[LoopState | None] = ContextVar("background_loop", default=None)


def loop_pass() -> None:
    """Цикл удачно прошёл круг. Вне фонового цикла ничего не делает."""
    state = _current.get()
    if state is not None:
        state.last_pass = time.time()


class _LoopErrorHandler(logging.Handler):
    """Ошибку, записанную в лог изнутри фонового цикла, засчитывает этому циклу."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        state = _current.get()
        if state is None:
            return
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            text = f"{text}: {type(error).__name__}: {error}"
        state.last_error_at = record.created
        state.last_error = text[:_ERROR_TEXT_LIMIT]


_error_handler = _LoopErrorHandler()


class BackgroundTasks:
    def __init__(self, registry: LoopRegistry | None = None, *, restart_delay: float = RESTART_DELAY) -> None:
        self.registry = registry or LoopRegistry()
        self._restart_delay = restart_delay
        self._tasks: list[asyncio.Task[None]] = []
        root = logging.getLogger()
        if _error_handler not in root.handlers:
            root.addHandler(_error_handler)

    def start(self, name: str, loop: Callable[[], Awaitable[None]]) -> None:
        """Запустить цикл: loop() вызывается заново после каждого падения или выхода."""
        state = LoopState(started_at=time.time())
        self.registry.loops[name] = state
        self._tasks.append(asyncio.create_task(self._supervise(name, loop, state), name=name))

    async def _supervise(self, name: str, loop: Callable[[], Awaitable[None]], state: LoopState) -> None:
        # У задачи свой контекст: и цикл, и задачи, которые он запускает, пишут ошибки в state
        _current.set(state)
        while True:
            try:
                await loop()
                logger.error("Фоновый цикл %s завершился сам — перезапуск через %.0f с", name, self._restart_delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Фоновый цикл %s упал — перезапуск через %.0f с", name, self._restart_delay)
            await asyncio.sleep(self._restart_delay)
            state.restarts += 1
            state.started_at = time.time()

    async def stop(self) -> None:
        """Остановить все циклы и дождаться их завершения."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
