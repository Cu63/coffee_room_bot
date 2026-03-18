"""Хендлер слотов через встроенный Telegram dice emoji 🎰."""

from __future__ import annotations

import asyncio
import logging
import math
import time

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
from bot.presentation.utils import NO_PREVIEW, check_gameban, reply_and_delete, schedule_delete

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


def _get_slots(value: int) -> tuple[int, int, int]:
    v = value - 1
    return v % 4, (v // 4) % 4, (v // 16) % 4


def _get_outcome(value: int) -> tuple[str, float]:
    if value == _JACKPOT_VALUE:  # 64 = 7 7 7
        return "jackpot", _MULT_JACKPOT
    s1, s2, s3 = _get_slots(value)
    if s1 == s2 == s3:  # три одинаковых: 1, 22, 43
        return "win", _MULT_WIN
    if s1 == s2 or s2 == s3 or s1 == s3:  # два одинаковых — любая пара
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

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, message.from_user.id, message.chat.id, formatter._t)
    if ban_msg:
        await reply_and_delete(message, ban_msg)
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    p = formatter._p
    sc = config.slots
    bot_id = bot.id

    if not command.args:
        cooldown_str = (
            f"\n⏳ Кулдаун: {sc.cooldown_minutes} мин. между спинами"
            if sc.cooldown_minutes > 0
            else ""
        )
        bot_balance = await score_service.get_bot_balance(bot_id, chat_id)
        await reply_and_delete(
            message,
            f"🎰 <b>Слоты</b>\n\n"
            f"Использование: /slots &lt;ставка&gt;\n"
            f"Ставка: от {sc.min_bet} до {sc.max_bet} {p.pluralize(sc.max_bet)}\n"
            f"Ставка <b>all</b> — весь баланс (1 раз в сутки)"
            f"{cooldown_str}\n\n"
            f"<b>Выплаты:</b>\n"
            f"  🎰 Джекпот (777) — ×{_MULT_JACKPOT}\n"
            f"  🏆 Три одинаковых — ×{_MULT_WIN}\n"
            f"  😬 Частичное совпадение — возврат {int(_MULT_NEAR_MISS * 100)}%\n"
            f"  💸 Проигрыш — ставка сгорает\n\n"
            f"💰 Баланс бота: <b>{bot_balance}</b> {p.pluralize(bot_balance)}",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    raw_arg = command.args.strip().lower()

    # ── Определяем ставку ─────────────────────────────────────────
    is_all_bet = raw_arg == "all"

    if is_all_bet:
        # Проверяем дневной лимит all-ставки
        if await store.slots_all_used_today(user_id, chat_id):
            await reply_and_delete(
                message,
                "🎰 Ставку <b>all</b> можно использовать только 1 раз в сутки.",
                parse_mode=ParseMode.HTML,
            )
            return

        score = await score_service.get_score(user_id, chat_id)
        bet = score.value
        if bet <= 0:
            await reply_and_delete(
                message,
                f"Недостаточно баллов для ставки all. У тебя: {score.value} {p.pluralize(score.value)}.",
            )
            return
    else:
        try:
            bet = int(raw_arg)
        except ValueError:
            await reply_and_delete(
                message,
                "Ставка должна быть числом или <b>all</b>.",
                parse_mode=ParseMode.HTML,
            )
            return

        if bet < sc.min_bet or bet > sc.max_bet:
            await reply_and_delete(
                message,
                f"Ставка: от {sc.min_bet} до {sc.max_bet} {p.pluralize(sc.max_bet)}.",
            )
            return

    # ── Проверяем кулдаун ─────────────────────────────────────────
    cooldown_seconds = sc.cooldown_minutes * 60
    if cooldown_seconds > 0:
        can_play = await store.slots_cooldown_check(user_id, chat_id, cooldown_seconds)
        if not can_play:
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

    # ── Проверяем баланс (для обычной ставки) ────────────────────
    if not is_all_bet:
        score = await score_service.get_score(user_id, chat_id)
        if score.value < bet:
            await reply_and_delete(
                message,
                f"Недостаточно баллов. У тебя: {score.value} {p.pluralize(score.value)}.",
            )
            return

    # ── Устанавливаем кулдаун и отмечаем all-ставку ──────────────
    if cooldown_seconds > 0:
        await store.slots_cooldown_set(user_id, chat_id, cooldown_seconds)
    if is_all_bet:
        await store.slots_all_mark_used(user_id, chat_id)

    # ── Списываем ставку ──────────────────────────────────────────
    await score_service.add_score(user_id, chat_id, -bet, admin_id=user_id)

    # ── Запускаем анимацию ────────────────────────────────────────
    dice_msg = await message.answer_dice(emoji="🎰")
    value = dice_msg.dice.value  # 1–64

    await asyncio.sleep(3)

    outcome, multiplier = _get_outcome(value)
    raw_payout = int(bet * multiplier)  # что выплатилось бы без ограничений

    # ── Рассчитываем выплату с учётом баланса бота ────────────────
    #
    # При выигрыше (win/jackpot):
    #   Возврат ставки — всегда (деньги уже списаны с игрока)
    #   Чистый выигрыш (raw_payout - bet) — ограничен балансом бота
    #
    # При near_miss:
    #   Возврат части ставки (0.8*bet) начисляется игроку
    #   Разница (0.2*bet) идёт боту
    #
    # При loss:
    #   Вся ставка идёт боту

    actual_payout = raw_payout
    payout_capped = False

    if outcome in ("win", "jackpot"):
        net_gain = raw_payout - bet
        bot_balance = await score_service.get_bot_balance(bot_id, chat_id)
        actual_gain = min(net_gain, max(bot_balance, 0))
        actual_payout = bet + actual_gain  # возврат ставки + то, что бот реально платит
        payout_capped = actual_gain < net_gain

        # Игрок получает возврат ставки + фактический выигрыш
        await score_service.add_score(user_id, chat_id, actual_payout, admin_id=user_id)
        # Бот платит actual_gain
        if actual_gain > 0:
            await score_service.add_score(bot_id, chat_id, -actual_gain, admin_id=bot_id)

    elif outcome == "near_miss":
        actual_payout = raw_payout  # 0.8 * bet
        await score_service.add_score(user_id, chat_id, actual_payout, admin_id=user_id)
        bot_gain = bet - actual_payout  # 0.2 * bet -> боту
        if bot_gain > 0:
            await score_service.add_score(bot_id, chat_id, bot_gain, admin_id=bot_id)

    else:  # loss
        actual_payout = 0
        # Вся ставка -> боту
        await score_service.add_score(bot_id, chat_id, bet, admin_id=bot_id)

    # ── Статистика ────────────────────────────────────────────────
    if outcome in ("jackpot", "win"):
        await stats_repo.add_win(user_id, chat_id, "slots")

    # ── Итоговый баланс игрока ────────────────────────────────────
    final_score = await score_service.get_score(user_id, chat_id)
    balance = final_score.value
    bal_str = f"{balance} {p.pluralize(balance)}"

    # ── Формируем строку результата ───────────────────────────────
    all_prefix = "🃏 <b>Ставка ВСЁ!</b> " if is_all_bet else ""

    if outcome == "jackpot":
        win_net = actual_payout - bet
        if payout_capped:
            max_possible = int(bet * _MULT_JACKPOT) - bet
            result_line = (
                f"{all_prefix}🎰 <b>ДЖЕКПОТ!</b> Но у бота не хватает баллов...\n"
                f"Мог выиграть <b>{max_possible}</b>, получил <b>{win_net}</b> {p.pluralize(win_net)} 🤑"
            )
        else:
            result_line = f"{all_prefix}🎰 <b>ДЖЕКПОТ!</b> Ты выиграл <b>{win_net}</b> {p.pluralize(win_net)}! 🤑"

    elif outcome == "win":
        win_net = actual_payout - bet
        if payout_capped:
            max_possible = int(bet * _MULT_WIN) - bet
            result_line = (
                f"{all_prefix}🏆 Три одинаковых! Но у бота не хватает баллов...\n"
                f"Мог выиграть <b>{max_possible}</b>, получил <b>{win_net}</b> {p.pluralize(win_net)}."
            )
        else:
            result_line = f"{all_prefix}🏆 Три одинаковых! Ты выиграл <b>{win_net}</b> {p.pluralize(win_net)}!"

    elif outcome == "near_miss":
        result_line = (
            f"{all_prefix}😬 Почти... Возвращаю {actual_payout} {p.pluralize(actual_payout)} "
            f"({int(_MULT_NEAR_MISS * 100)}% ставки)."
        )

    else:  # loss
        result_line = (
            f"{all_prefix}💸 Мимо. Потерял <b>{bet}</b> {p.pluralize(bet)}."
        )

    result_msg = await message.answer(
        f"{result_line}\nБаланс: {bal_str}",
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
    schedule_delete(bot, message, dice_msg, result_msg, delay=30)