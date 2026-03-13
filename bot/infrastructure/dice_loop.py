"""Фоновая задача: бросает кости за участников по истечении времени сбора."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot

from bot.domain.tz import TZ_MSK
from bot.presentation.utils import schedule_delete, schedule_delete_id

logger = logging.getLogger(__name__)

_DICE_EMOJI = "🎲"
_GAME_DELETE_DELAY = 30  # секунд после завершения игры


async def dice_loop(bot: Bot, container) -> None:
    """Каждые 5 секунд проверяет и завершает просроченные игры в кости."""
    from bot.application.dice_service import DiceService

    while True:
        await asyncio.sleep(5)
        try:
            async with container() as scope:
                service: DiceService = await scope.get(DiceService)
                expired = await service.get_expired(datetime.now(TZ_MSK))

            for game in expired:
                logger.info("Auto-finishing dice game %d in chat %d", game.id, game.chat_id)
                await _resolve_game(bot, game, container)
        except Exception:
            logger.exception("Error in dice_loop")


async def _resolve_game(bot: Bot, game, container) -> None:
    """Бросает кости за каждого участника, распределяет приз."""
    from bot.application.dice_service import DiceService
    from bot.domain.pluralizer import ScorePluralizer

    # Получаем список участников до броска
    async with container() as scope:
        service: DiceService = await scope.get(DiceService)
        participants = await service.get_participants(game.id)

    if not participants:
        async with container() as scope:
            service = await scope.get(DiceService)
            await service.finish(game.id, {})
        cancel_msg = await bot.send_message(game.chat_id, "🎲 Игра в кости отменена: нет участников.")
        schedule_delete(bot, cancel_msg, delay=_GAME_DELETE_DELAY)
        await _remove_lobby(bot, game.chat_id, game.message_id)
        return

    # Объявляем начало бросков
    announce_msg = await bot.send_message(game.chat_id, "🎲 Бросаем кости!")
    schedule_delete(bot, announce_msg, delay=_GAME_DELETE_DELAY)

    # Бросаем кости за каждого участника
    dice_results: dict[int, int] = {}

    for user_id in participants:
        try:
            msg = await bot.send_dice(chat_id=game.chat_id, emoji=_DICE_EMOJI)
            dice_results[user_id] = msg.dice.value  # type: ignore[union-attr]
            schedule_delete(bot, msg, delay=_GAME_DELETE_DELAY)
            await asyncio.sleep(0.5)
        except Exception:
            logger.warning("Failed to send dice for user %d in game %d", user_id, game.id)

    # Завершаем игру и распределяем призы
    async with container() as scope:
        service = await scope.get(DiceService)
        pluralizer: ScorePluralizer = await scope.get(ScorePluralizer)
        result = await service.finish(game.id, dice_results)

    if result is None:
        logger.warning("Game %d already finished when trying to resolve", game.id)
        return

    await _post_dice_results(bot, result, pluralizer)
    await _remove_lobby(bot, game.chat_id, game.message_id)


async def _post_dice_results(bot: Bot, result, pluralizer) -> None:
    """Публикует итоговое сообщение с результатами игры."""
    chat_id = result.game.chat_id
    participants_count = len(result.participants)

    lines = [f"🎲 <b>Итоги игры в кости!</b> Участников: {participants_count}\n"]

    # Результаты бросков
    for user_id in result.participants:
        value = result.dice_results.get(user_id, "?")
        is_winner = user_id in result.winners
        crown = " 👑" if is_winner else ""
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            name = f'<a href="tg://user?id={user_id}">{member.user.full_name}</a>'
        except Exception:
            name = f"<code>{user_id}</code>"
        lines.append(f"• {name}: {value}{crown}")

    lines.append("")

    score_word = pluralizer.pluralize(result.prize_per_winner)
    if len(result.winners) == 1:
        winner_id = result.winners[0]
        try:
            member = await bot.get_chat_member(chat_id, winner_id)
            winner_name = f'<a href="tg://user?id={winner_id}">{member.user.full_name}</a>'
        except Exception:
            winner_name = f"<code>{winner_id}</code>"
        lines.append(f"🏆 {winner_name} выигрывает <b>{result.prize_per_winner} {score_word}</b>!")
    else:
        winner_mentions = []
        for uid in result.winners:
            try:
                member = await bot.get_chat_member(chat_id, uid)
                winner_mentions.append(f'<a href="tg://user?id={uid}">{member.user.full_name}</a>')
            except Exception:
                winner_mentions.append(f"<code>{uid}</code>")
        winners_str = ", ".join(winner_mentions)
        lines.append(
            f"🏆 Победители: {winners_str}\n"
            f"💰 Приз: <b>{result.prize_per_winner} {score_word}</b> каждому"
        )

    result_msg = await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    schedule_delete(bot, result_msg, delay=_GAME_DELETE_DELAY)


async def _remove_lobby(bot: Bot, chat_id: int, message_id: int | None) -> None:
    """Убирает кнопку у лобби-сообщения и планирует его удаление."""
    if not message_id:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
    except Exception:
        pass
    schedule_delete_id(bot, chat_id, message_id, delay=_GAME_DELETE_DELAY)
