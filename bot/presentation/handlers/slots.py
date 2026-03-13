"""Хендлер слотов через встроенный Telegram dice emoji 🎰."""

from __future__ import annotations

import asyncio
import logging
import math

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.score_service import ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, schedule_delete

logger = logging.getLogger(__name__)
router = Router(name="slots")

# ── Таблица исходов по значению dice (1–64) ─────────────────────
#
# Telegram slot machine возвращает значение 1–64.
# Известные «три одинаковых»:
#   1  = BAR BAR BAR
#   22 = GRAPE GRAPE GRAPE
#   43 = LEMON LEMON LEMON
#   64 = SEVEN SEVEN SEVEN (джекпот)
# Значения 2–32 (кроме 22) считаются «частичным совпадением».
# Значения 33–63 (кроме 43) — проигрыш.
#
# RTP ≈ 122% (намеренно завышен для веселья).

_JACKPOT_VALUE = 64
_THREE_OF_KIND = {1, 22, 43}
_NEAR_MISS_MIN = 2
_NEAR_MISS_MAX = 32

# Множители: сколько ставок возвращается игроку
_MULT_JACKPOT = 30     # net: +29×bet
_MULT_WIN = 8          # net: +7×bet
_MULT_NEAR_MISS = 0.8  # net: -0.2×bet (возврат 80%)
_MULT_LOSS = 0.0       # net: -1×bet


def _get_outcome(value: int) -> tuple[str, float]:
    """Возвращает (название_исхода, множитель_возврата)."""
    if value == _JACKPOT_VALUE:
        return "jackpot", _MULT_JACKPOT
    if value in _THREE_OF_KIND:
        return "win", _MULT_WIN
    if _NEAR_MISS_MIN <= value <= _NEAR_MISS_MAX:
        return "near_miss", _MULT_NEAR_MISS
    return "loss", _MULT_LOSS


@router.message(Command("slots"))
@inject
async def cmd_slots(
    message: Message,
    bot: Bot,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    stats_repo: FromDishka[IUserStatsRepository],
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    p = formatter._p
    sc = config.slots

    if not command.args:
        cooldown_str = (
            f"\n\n⏳ Кулдаун: {sc.cooldown_minutes} мин. между спинами"
            if sc.cooldown_minutes > 0
            else ""
        )
        await reply_and_delete(
            message,
            f"🎰 <b>Слоты</b>\n\n"
            f"Использование: /slots &lt;ставка&gt;\n"
            f"Ставка: от {sc.min_bet} до {sc.max_bet} {p.pluralize(sc.max_bet)}"
            f"{cooldown_str}\n\n"
            f"<b>Выплаты:</b>\n"
            f"  🎰 Джекпот (777) — ×{_MULT_JACKPOT}\n"
            f"  🏆 Три одинаковых — ×{_MULT_WIN}\n"
            f"  😬 Частичное совпадение — возврат {int(_MULT_NEAR_MISS * 100)}%\n"
            f"  💸 Проигрыш — ставка сгорает",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    try:
        bet = int(command.args.strip())
    except ValueError:
        await reply_and_delete(message, "Ставка должна быть числом.")
        return

    if bet < sc.min_bet or bet > sc.max_bet:
        await reply_and_delete(message, f"Ставка: от {sc.min_bet} до {sc.max_bet} {p.pluralize(sc.max_bet)}.")
        return

    # Проверяем кулдаун перед списанием ставки
    cooldown_seconds = sc.cooldown_minutes * 60
    if cooldown_seconds > 0:
        can_play = await store.slots_cooldown_check(user_id, chat_id, cooldown_seconds)
        if not can_play:
            # Вычисляем, сколько минут осталось
            import time
            key_raw = await store._r.get(f"slots:last:{user_id}:{chat_id}")
            if key_raw is not None:
                elapsed = time.time() - float(key_raw)
                remaining = math.ceil((cooldown_seconds - elapsed) / 60)
            else:
                remaining = sc.cooldown_minutes
            await reply_and_delete(
                message,
                formatter._t["slots_cooldown"].format(minutes=remaining),
            )
            return

    score = await score_service.get_score(user_id, chat_id)
    if score.value < bet:
        await reply_and_delete(message, f"Недостаточно баллов. У тебя: {score.value} {p.pluralize(score.value)}.")
        return

    # Устанавливаем кулдаун сразу после всех проверок
    if cooldown_seconds > 0:
        await store.slots_cooldown_set(user_id, chat_id, cooldown_seconds)

    # Списываем ставку
    await score_service.add_score(user_id, chat_id, -bet, admin_id=user_id)

    # Запускаем анимацию — значение приходит сразу в ответе
    dice_msg = await message.answer_dice(emoji="🎰")
    value = dice_msg.dice.value  # 1–64

    # Ждём чуть дольше анимации перед объявлением результата
    await asyncio.sleep(3)

    outcome, multiplier = _get_outcome(value)
    payout = int(bet * multiplier)

    if payout > 0:
        await score_service.add_score(user_id, chat_id, payout, admin_id=user_id)

    if outcome in ("jackpot", "win"):
        await stats_repo.add_win(user_id, chat_id, "slots")

    new_balance = await score_service.get_score(user_id, chat_id)
    bal_str = f"{new_balance.value} {p.pluralize(new_balance.value)}"

    if outcome == "jackpot":
        win_net = payout - bet
        result_line = f"🎰 <b>ДЖЕКПОТ!</b> Ты выиграл <b>{win_net}</b> {p.pluralize(win_net)}! 🤑"
    elif outcome == "win":
        win_net = payout - bet
        result_line = f"🏆 Три одинаковых! Ты выиграл <b>{win_net}</b> {p.pluralize(win_net)}!"
    elif outcome == "near_miss":
        result_line = (
            f"😬 Почти... Возвращаю {payout} {p.pluralize(payout)} "
            f"({int(_MULT_NEAR_MISS * 100)}% ставки)."
        )
    else:
        result_line = f"💸 Мимо. Потерял <b>{bet}</b> {p.pluralize(bet)}."

    result_msg = await message.answer(
        f"{result_line}\nБаланс: {bal_str}",
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
    schedule_delete(bot, message, dice_msg, result_msg, delay=30)
