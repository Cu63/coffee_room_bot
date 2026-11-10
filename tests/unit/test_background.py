"""Фоновые циклы под присмотром: упавший цикл перезапускается, а не умирает тихо."""

import asyncio
import logging

from bot.infrastructure.background import BackgroundTasks, loop_pass


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


async def test_registry_remembers_passes_crashes_and_restarts():
    runs = 0
    second_run = asyncio.Event()

    async def loop() -> None:
        nonlocal runs
        runs += 1
        loop_pass()
        if runs == 1:
            raise RuntimeError("зависимость не получена")
        second_run.set()
        await asyncio.Event().wait()

    tasks = BackgroundTasks(restart_delay=0)
    tasks.start("demo", loop)
    await asyncio.wait_for(second_run.wait(), 1)
    state = tasks.registry.loops["demo"]
    await tasks.stop()

    assert state.restarts == 1
    assert state.last_pass is not None
    # Падение записал надзиратель — это тоже ошибка цикла
    assert state.last_error is not None and state.last_error.startswith("Фоновый цикл demo упал")
    assert "RuntimeError: зависимость не получена" in state.last_error


async def test_errors_logged_inside_a_loop_count_for_it():
    logged = asyncio.Event()

    async def loop() -> None:
        try:
            raise ValueError("игра не рассчитана")
        except ValueError:
            # Как в циклах: ошибку одного элемента пишут в лог и идут дальше
            logging.getLogger("bot.infrastructure.demo_loop").exception("cleanup: не удалось рассчитать игру")
        logged.set()
        await asyncio.Event().wait()

    tasks = BackgroundTasks(restart_delay=0)
    tasks.start("demo", loop)
    await asyncio.wait_for(logged.wait(), 1)
    # Ошибка вне фоновых циклов (в хендлере) ни одному циклу не засчитывается
    logging.getLogger("bot.presentation.demo").error("ошибка хендлера")
    state = tasks.registry.loops["demo"]
    await tasks.stop()

    assert state.restarts == 0
    assert state.last_error == "cleanup: не удалось рассчитать игру: ValueError: игра не рассчитана"
    assert state.last_pass is None


async def test_loop_pass_outside_loops_does_nothing():
    loop_pass()  # хендлер или тест: цикла в контексте нет — и падать не из-за чего
