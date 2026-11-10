"""/status — состояние бота для админов бота: версия, базы, фоновые циклы."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections.abc import Awaitable

import asyncpg
import redis.asyncio as aioredis
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.domain.bot_utils import format_duration, is_admin
from bot.infrastructure.background import LoopRegistry, LoopState
from bot.infrastructure.config_loader import AppConfig, BotSettings
from bot.infrastructure.health import ping_postgres, ping_redis
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.utils import reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="status")

_ERROR_SHOWN_FOR = 24 * 3600  # ошибку старше суток уже не показываем


async def _database(name: str, ping: Awaitable[float], t: dict[str, str]) -> str:
    try:
        seconds = await ping
    except Exception as e:
        logger.warning("/status: %s не ответил", name, exc_info=True)
        return t["status_db_down"].format(name=name, error=html.escape(type(e).__name__))
    return t["status_db_ok"].format(name=name, ms=round(seconds * 1000))


def _loop_line(name: str, state: LoopState, now: float, t: dict[str, str]) -> str:
    if state.last_pass is None:
        line = t["status_loop_no_pass"].format(name=name)
    else:
        line = t["status_loop_pass"].format(name=name, ago=format_duration(int(now - state.last_pass)))
    if state.restarts:
        line += t["status_loop_restarts"].format(count=state.restarts)
    if state.last_error_at is not None and now - state.last_error_at < _ERROR_SHOWN_FOR:
        line += "\n" + t["status_loop_error"].format(
            ago=format_duration(int(now - state.last_error_at)), error=html.escape(state.last_error or "")
        )
    return line


@router.message(Command("status"))
@inject
async def cmd_status(
    message: Message,
    config: FromDishka[AppConfig],
    settings: FromDishka[BotSettings],
    loops: FromDishka[LoopRegistry],
    pool: FromDishka[asyncpg.Pool],
    redis: FromDishka[aioredis.Redis],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Версия и аптайм, отвечают ли Postgres и Redis, как идут фоновые циклы. Только для админов бота."""
    t = formatter._t
    if not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["admin_not_allowed"])
        return

    postgres, redis_line = await asyncio.gather(
        _database("Postgres", ping_postgres(pool), t), _database("Redis", ping_redis(redis), t)
    )
    now = time.time()
    lines = [
        t["status_header"].format(
            version=html.escape(settings.bot_version or t["status_version_unknown"]),
            uptime=format_duration(int(now - loops.started_at)),
        ),
        postgres,
        redis_line,
        "",
        t["status_loops_header"],
    ]
    if loops.loops:
        lines += [_loop_line(name, state, now, t) for name, state in sorted(loops.loops.items())]
    else:
        lines.append(t["status_no_loops"])
    await reply_and_delete(message, "\n".join(lines), parse_mode=ParseMode.HTML)
