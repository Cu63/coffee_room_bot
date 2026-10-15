"""Фоновые циклы под присмотром: упавший цикл перезапускается, а не умирает тихо."""

import asyncio
import logging

from bot.infrastructure.background import BackgroundTasks


async def test_crashed_loop_is_logged_and_restarted(caplog):
    runs = 0
    restarted = asyncio.Event()

    async def loop() -> None:
        nonlocal runs
        runs += 1
        if runs == 1:
            raise RuntimeError("зависимость не получена")
        restarted.set()
        await asyncio.Event().wait()  # работает до остановки

    tasks = BackgroundTasks(restart_delay=0)
    with caplog.at_level(logging.ERROR, logger="bot.infrastructure.background"):
        tasks.start("demo", loop)
        await asyncio.wait_for(restarted.wait(), 1)
        await tasks.stop()

    assert runs == 2
    assert "Фоновый цикл demo упал" in caplog.text


async def test_stop_cancels_and_waits_for_loops():
    cancelled = asyncio.Event()

    async def loop() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    tasks = BackgroundTasks(restart_delay=0)
    tasks.start("demo", loop)
    await asyncio.sleep(0)
    await tasks.stop()
    assert cancelled.is_set()
