"""Пасхалка «67».

- Ровно «67» 6-го числа месяца (МСК), первый раз за день → +67 кирчиков
  с коротким сообщением.
- «67» второй и последующий раз за любой календарный день → тихий мут
  на 67 минут (без каких-либо сообщений, от лица бота, без списания баллов).
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.mute_service import MuteService
from bot.application.score_service import ScoreService
from bot.domain.tz import now_msk
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._admin_utils import apply_mute

router = Router(name="sixtyseven")


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"^\s*67\s*$"),
)
@inject
async def on_sixtyseven(
    message: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    mute_service: FromDishka[MuteService],
    config: FromDishka[AppConfig],
    formatter: FromDishka[MessageFormatter],
) -> None:
    cfg = config.sixtyseven
    if not cfg.enabled or message.from_user is None:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    now = now_msk()

    count = await store.sixtyseven_incr(user_id, chat_id, now.strftime("%Y%m%d"))

    if count == 1:
        # Первый «67» за день — награда только в день награды (6-е число МСК)
        if now.day == cfg.reward_day:
            await score_service.add_score(user_id, chat_id, cfg.reward, admin_id=bot.id)
            link = user_link(
                message.from_user.username, message.from_user.full_name or "", user_id
            )
            await message.reply(
                formatter._t["sixtyseven_reward"].format(user=link, amount=cfg.reward),
                parse_mode=ParseMode.HTML,
            )
        return

    # Второй и последующий «67» за день → тихий мут, без сообщений
    until = now + timedelta(minutes=cfg.mute_minutes)
    await apply_mute(
        bot,
        mute_service,
        target_id=user_id,
        chat_id=chat_id,
        muted_by=bot.id,
        until=until,
    )
