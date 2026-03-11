"""Logging handler, который отправляет записи в Telegram-чат."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from aiogram import Bot


class TelegramLogHandler(logging.Handler):
    """Асинхронный handler для отправки логов в Telegram.

    Буферизирует сообщения и отправляет их батчами,
    чтобы не упираться в rate-limit Telegram API.
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        level: int = logging.ERROR,
        flush_interval: float = 5.0,
        max_buffer: int = 50,
    ) -> None:
        super().__init__(level)
        self._bot = bot
        self._chat_id = chat_id
        self._buffer: deque[str] = deque(maxlen=max_buffer)
        self._flush_interval = flush_interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Запустить фоновую отправку."""
        if self._task is None:
            self._task = asyncio.create_task(self._flush_loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            # Обрезаем до лимита Telegram (4096)
            if len(msg) > 4000:
                msg = msg[:4000] + "..."
            self._buffer.append(msg)
        except Exception:
            self.handleError(record)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        # Собираем всё из буфера в одно сообщение
        lines = []
        total = 0
        while self._buffer:
            line = self._buffer.popleft()
            if total + len(line) + 1 > 4000:
                # Отправляем то что набрали
                await self._send("\n".join(lines))
                lines = []
                total = 0
            lines.append(line)
            total += len(line) + 1
        if lines:
            await self._send("\n".join(lines))

    async def _send(self, text: str) -> None:
        try:
            await self._bot.send_message(
                self._chat_id,
                f"<pre>{_escape_html(text)}</pre>",
                parse_mode="HTML",
            )
        except Exception:
            pass  # не ломаем приложение из-за логирования


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
