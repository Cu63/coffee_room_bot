from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.dice_service import DiceService
from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.bot_utils import format_duration, parse_duration
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import check_gameban, reply_and_delete, safe_callback_answer

logger = logging.getLogger(__name__)

router = Router(name="dice")


def _join_kb(game_id: int, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎲 Участвовать ({count})",
                    callback_data=f"dice:join:{game_id}",
                )
            ]
        ]
    )


# ─── /dice ───────────────────────────────────────────────────────────────────


@router.message(Command("dice"))
@inject
async def cmd_dice(
    message: Message,
    service: FromDishka[DiceService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    user_repo: FromDishka[IUserRepository],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
) -> None:
    from datetime import datetime, timedelta

    if message.from_user is None:
        return

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, message.from_user.id, message.chat.id, formatter._t)
    if ban_msg:
        await reply_and_delete(message, ban_msg)
        return

    args = (message.text or "").split()[1:]
    if len(args) < 2:
        await reply_and_delete(
            message,
            "🎲 <b>Игра в кости</b>\n\n"
            "Использование: <code>/dice &lt;ставка&gt; &lt;время&gt;</code>\n"
            "Пример: <code>/dice 10 2m</code>\n\n"
            f"Ставка: от {config.dice.min_bet} до {config.dice.max_bet} {pluralizer.pluralize(config.dice.max_bet)}\n"
            f"Время сбора: от {format_duration(config.dice.min_wait_seconds)} "
            f"до {format_duration(config.dice.max_wait_seconds)}",
            parse_mode="HTML",
        )
        return

    # Парсим ставку
    try:
        bet = int(args[0])
        if bet <= 0:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, "❌ Ставка должна быть положительным числом.")
        return

    if bet < config.dice.min_bet:
        sw = pluralizer.pluralize(config.dice.min_bet)
        await reply_and_delete(message, f"❌ Минимальная ставка: {config.dice.min_bet} {sw}.")
        return

    if bet > config.dice.max_bet:
        sw = pluralizer.pluralize(config.dice.max_bet)
        await reply_and_delete(message, f"❌ Максимальная ставка: {config.dice.max_bet} {sw}.")
        return

    # Парсим время ожидания
    wait_seconds = parse_duration(args[1])
    if wait_seconds is None or wait_seconds <= 0:
        await reply_and_delete(
            message,
            "❌ Неверный формат времени. Примеры: <code>30s</code>, <code>1m</code>, <code>2m30s</code>",
            parse_mode="HTML",
        )
        return

    if wait_seconds < config.dice.min_wait_seconds:
        await reply_and_delete(
            message,
            f"❌ Минимальное время ожидания: {format_duration(config.dice.min_wait_seconds)}.",
        )
        return

    if wait_seconds > config.dice.max_wait_seconds:
        await reply_and_delete(
            message,
            f"❌ Максимальное время ожидания: {format_duration(config.dice.max_wait_seconds)}.",
        )
        return

    # Upsert пользователя
    await user_repo.upsert(
        User(
            id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    )

    ends_at = datetime.now(TZ_MSK) + timedelta(seconds=wait_seconds)
    result = await service.create(
        chat_id=message.chat.id,
        created_by=message.from_user.id,
        bet=bet,
        ends_at=ends_at,
    )

    if result.user_already_in_game:
        await reply_and_delete(message, "❌ Ты уже участвуешь в активной игре в этом чате. Дождись её завершения.")
        return

    if result.not_enough or result.game is None:
        sw = pluralizer.pluralize(bet)
        score_word_many = pluralizer.pluralize(0)  # форма для "много"
        await reply_and_delete(
            message,
            f"❌ Недостаточно {score_word_many}. Нужно: {bet} {sw}.",
        )
        return

    game = result.game
    sw_bet = pluralizer.pluralize(bet)
    wait_str = format_duration(wait_seconds)

    text = (
        "🎲 <b>Игра в кости!</b>\n\n"
        f"Ставка: <b>{bet} {sw_bet}</b>\n"
        f"Сбор участников: <b>{wait_str}</b>\n\n"
        f"Участники: 1\n"
        f"Нажми кнопку, чтобы сделать ставку и войти в игру!"
    )
    sent = await message.answer(text, parse_mode="HTML", reply_markup=_join_kb(game.id, 1))
    await service.set_message_id(game.id, sent.message_id)


# ─── Callback: кнопка «Участвовать» ─────────────────────────────────────────


@router.callback_query(F.data.startswith("dice:join:"))
@inject
async def cb_dice_join(
    cb: CallbackQuery,
    service: FromDishka[DiceService],
    pluralizer: FromDishka[ScorePluralizer],
    user_repo: FromDishka[IUserRepository],
    store: FromDishka[RedisStore],
    formatter: FromDishka[MessageFormatter],
) -> None:
    game_id = int(cb.data.split(":")[2])
    user_id = cb.from_user.id

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, user_id, cb.message.chat.id, formatter._t)
    if ban_msg:
        await safe_callback_answer(cb, ban_msg, show_alert=True)
        return

    # Upsert пользователя — иначе FK на scores упадёт при списании баллов
    await user_repo.upsert(
        User(
            id=user_id,
            username=cb.from_user.username,
            full_name=cb.from_user.full_name,
        )
    )

    join_result = await service.join(game_id, user_id)

    if join_result.game_not_found:
        await cb.answer("Игра уже завершена или не найдена.", show_alert=False)
        return

    if join_result.already_joined:
        await cb.answer("Ты уже участвуешь в этой игре.", show_alert=False)
        return

    if join_result.already_in_other_game:
        await cb.answer("Ты уже участвуешь в другой активной игре в этом чате.", show_alert=True)
        return

    if join_result.not_enough:
        sw = pluralizer.pluralize(join_result.bet)
        score_word_many = pluralizer.pluralize(0)
        await cb.answer(
            f"Недостаточно {score_word_many}. Нужно: {join_result.bet} {sw}, "
            f"у вас: {join_result.balance} {pluralizer.pluralize(join_result.balance)}.",
            show_alert=True,
        )
        return

    count = await service.count_participants(game_id)
    await cb.answer("✅ Ты в игре! Удачи 🎲", show_alert=False)

    try:
        await cb.message.edit_reply_markup(reply_markup=_join_kb(game_id, count))
    except Exception:
        pass
