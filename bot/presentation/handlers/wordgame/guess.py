"""Reply на игровое сообщение бота = попытка угадать слово."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.daily_leaderboard_repository import IDailyLeaderboardRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import now_msk
from bot.domain.wordgame_entities import (
    compare,
    is_valid_word,
    merge_revealed,
    normalize_word,
)
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers.wordgame.helpers import game_text, game_to_raw, raw_to_game
from bot.presentation.utils import schedule_delete, schedule_delete_id

router = Router(name="wordgame_guess")


# ── Reply на игровое сообщение бота = попытка угадать ───────────────────────

@router.message(
    F.reply_to_message.as_("replied"),
    F.reply_to_message.from_user.is_bot.is_(True),
    F.text,
    ~F.text.startswith("/"),
)
@inject
async def msg_reply_guess(
    message: Message,
    replied: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    lb_repo: FromDishka[IDailyLeaderboardRepository],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Ищем игру по message_id сообщения, на которое ответили
    raw = await store.wg_game_by_message_id(chat_id, replied.message_id)
    if raw is None:
        # Не наша игра — передаём следующему хендлеру (например, анаграмме)
        raise SkipHandler

    game = raw_to_game(raw)
    if game.finished or game.is_expired:
        return

    if game.creator_id == user_id and not game.is_random:
        err = await message.reply("❌ Нельзя угадывать свою игру!")
        schedule_delete(bot, err, message, delay=30)
        return

    guess = normalize_word(message.text or "")
    wg = config.wordgame

    if not is_valid_word(guess, wg.min_word_length, wg.max_word_length):
        err = await message.reply(
            f"❌ Только буквы, длина {wg.min_word_length}–{wg.max_word_length}."
        )
        schedule_delete(bot, err, message, delay=30)
        return

    if len(guess) != len(game.word):
        err = await message.reply(
            f"❌ Слово должно быть из <b>{len(game.word)}</b> букв, а не {len(guess)}.",
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(bot, err, message, delay=30)
        return

    await user_repo.upsert(User(
        id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    ))

    if game.already_tried(user_id, guess):
        err = await message.reply(
            f"🔄 «<b>{guess}</b>» ты уже пробовал. Другое слово!",
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(bot, err, message, delay=30)
        return

    user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'

    # Проверка баллов — только если ставка игры > 0 (иначе все попытки бесплатные)
    cost = wg.attempt_cost
    if game.bet > 0 and cost > 0:
        bal = await score_service.get_score(user_id, chat_id)
        if bal.value <= 0:
            err = await message.answer(
                f"🚫 {user_mention}: у тебя {bal.value} баллов — угадывать нельзя.",
                parse_mode=ParseMode.HTML,
            )
            schedule_delete(bot, err, message, delay=30)
            return

    matches = compare(game.word, guess)
    new_revealed = merge_revealed(game.revealed, matches)
    is_correct = all(matches)
    matched_count = sum(matches)
    had_revealed = game.revealed_count > 0  # были ли открытые буквы ДО этой попытки

    game.guesses.append({"user_id": user_id, "word": guess})
    game.revealed = new_revealed

    schedule_delete(bot, message, delay=30)

    if is_correct:
        # ── ПОБЕДА ──────────────────────────────────────────────────────
        game.finished = True
        game.winner_id = user_id
        await store.wg_game_finish(game.game_id)
        await store.wg_chat_remove(chat_id, game.game_id)

        await score_service.add_score(user_id, chat_id, game.bet, admin_id=user_id)

        # Записываем победу в дневной лидерборд
        game_type = "rword" if game.is_random else "word"
        try:
            await lb_repo.add_game_win(user_id, chat_id, game_type, now_msk().date())
        except Exception:
            pass  # не прерываем победу из-за ошибки статистики

        bet_str = pluralizer.pluralize(game.bet)
        win_text = (
            f"🎉 <b>Угадайка завершена!</b>\n\n"
            f"Слово: <b>{game.word}</b>\n"
            f"Угадал(а): {user_mention}\n"
            f"Приз: <b>+{game.bet} {bet_str}</b> 🏆\n"
            f"Количество попыток: <b>{len(game.guesses)}</b>"
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=game.message_id,
                text=win_text,
                parse_mode=ParseMode.HTML,
            )
            schedule_delete_id(bot, chat_id, game.message_id, delay=30)
        except TelegramBadRequest:
            result_msg = await bot.send_message(chat_id, win_text, parse_mode=ParseMode.HTML)
            schedule_delete(bot, result_msg, delay=30)

    else:
        # ── НЕВЕРНО — редактируем игровое сообщение с новой маской ──────
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=game.message_id,
                text=game_text(game, pluralizer),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass

        await store.wg_game_save_raw(game.game_id, game_to_raw(game))

        hint_parts = [
            f"❌ {user_mention}: «{guess}» — не то "
            f"({matched_count}/{len(game.word)} на месте)"
        ]

        if game.bet > 0 and cost > 0 and had_revealed:
            if game.is_random:
                await score_service.add_score(bot.id, chat_id, cost, admin_id=bot.id)
            else:
                await score_service.add_score(game.creator_id, chat_id, cost, admin_id=user_id)
            await score_service.add_score(user_id, chat_id, -cost, admin_id=user_id)
            hint_parts.append(f"<i>−{cost} балл за попытку</i>")

        hint = await message.answer("\n".join(hint_parts), parse_mode=ParseMode.HTML)
        schedule_delete(bot, hint, delay=15)
