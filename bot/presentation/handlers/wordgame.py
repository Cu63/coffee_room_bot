"""Обработчики игры «Угадайка».

Флоу:
1. /word <ставка> <время>  — в группе: создаёт игру, пишет создателю в ЛС
2. /start в ЛС — предлагает ввести слово если есть pending
3. Текст в ЛС — создатель отправляет загаданное слово
4. Reply на игровое сообщение бота — попытка угадать слово
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.wordgame_entities import (
    WordGame,
    compare,
    is_valid_word,
    merge_revealed,
    normalize_word,
)
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore
from bot.infrastructure.word_loader import pick_random_word
from bot.presentation.utils import reply_and_delete, schedule_delete, schedule_delete_id

logger = logging.getLogger(__name__)
router = Router(name="wordgame")

_DURATION_RE = re.compile(r"^(\d+)(m|h)$")


def _parse_duration_seconds(token: str) -> int | None:
    m = _DURATION_RE.match(token.lower())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    return value * 60 if unit == "m" else value * 3600


def _open_dm_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✏️ Написать слово в ЛС",
                url=f"https://t.me/{bot_username}",
            )
        ]]
    )


def _game_text(game: WordGame, pluralizer: ScorePluralizer) -> str:
    ends_dt = datetime.fromtimestamp(game.ends_at, tz=TZ_MSK)
    ends_str = ends_dt.strftime("%H:%M")
    bet_str = pluralizer.pluralize(game.bet)
    return (
        f"🔤 <b>Угадайка!</b>\n\n"
        f"Слово из <b>{len(game.word)}</b> букв: {game.masked}\n"
        f"Открыто: <b>{game.revealed_count}/{len(game.word)}</b>\n\n"
        f"💰 Ставка: <b>{game.bet} {bet_str}</b>  ⏰ До: <b>{ends_str}</b>\n\n"
        f"<i>Ответь на это сообщение словом, чтобы угадать</i>"
    )


def _raw_to_game(raw: dict) -> WordGame:
    return WordGame(
        game_id=raw["game_id"],
        chat_id=raw["chat_id"],
        creator_id=raw["creator_id"],
        word=raw["word"],
        bet=raw["bet"],
        ends_at=raw["ends_at"],
        revealed=raw.get("revealed", []),
        guesses=raw.get("guesses", []),
        message_id=raw.get("message_id", 0),
        finished=raw.get("finished", False),
        winner_id=raw.get("winner_id"),
        is_random=raw.get("is_random", False),
    )


def _game_to_raw(game: WordGame) -> dict:
    return {
        "game_id": game.game_id,
        "chat_id": game.chat_id,
        "creator_id": game.creator_id,
        "word": game.word,
        "bet": game.bet,
        "ends_at": game.ends_at,
        "revealed": game.revealed,
        "guesses": game.guesses,
        "message_id": game.message_id,
        "finished": game.finished,
        "winner_id": game.winner_id,
        "is_random": game.is_random,
    }


# ── /word <ставка> <время> ───────────────────────────────────────────────

@router.message(Command("word"))
@inject
async def cmd_wordgame(
    message: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    args = (message.text or "").split()[1:]
    wg = config.wordgame

    if len(args) < 2:
        await reply_and_delete(
            message,
            f"Использование: <code>/word &lt;ставка&gt; &lt;время&gt;</code>\n"
            f"Ставка: {wg.min_bet}–{wg.max_bet}  |  Время: от 3m до 1h\n\n"
            f"Пример: <code>/word 50 10m</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not args[0].isdigit():
        await reply_and_delete(message, "❌ Ставка должна быть числом.")
        return
    bet = int(args[0])
    if not (wg.min_bet <= bet <= wg.max_bet):
        await reply_and_delete(message, f"❌ Ставка: от {wg.min_bet} до {wg.max_bet}.")
        return

    duration_secs = _parse_duration_seconds(args[1])
    if duration_secs is None:
        await reply_and_delete(
            message,
            "❌ Неверный формат времени. Используй <code>Xm</code> или <code>Xh</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not (wg.min_duration_seconds <= duration_secs <= wg.max_duration_seconds):
        await reply_and_delete(
            message,
            f"❌ Время: от {wg.min_duration_seconds // 60}m "
            f"до {wg.max_duration_seconds // 60}m.",
        )
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    score = await score_service.get_score(user_id, chat_id)
    if score.value < bet:
        await reply_and_delete(
            message,
            f"❌ Недостаточно баллов. У тебя {score.value}, нужно {bet}.",
        )
        return

    window_secs = wg.game_window_hours * 3600
    created = await store.wg_rate_check(user_id, wg.max_games_per_window, window_secs)
    if created >= wg.max_games_per_window:
        await reply_and_delete(
            message,
            f"❌ Лимит: {wg.max_games_per_window} игры за {wg.game_window_hours} ч. Попробуй позже.",
        )
        return

    await user_repo.upsert(User(
        id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    ))

    bot_me = await bot.get_me()
    game_id = await store.wg_pending_create(
        user_id=user_id,
        chat_id=chat_id,
        bet=bet,
        duration_seconds=duration_secs,
    )
    await store.wg_awaiting_set(user_id, game_id)
    await store.wg_rate_record(user_id, window_secs)

    user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'
    bet_str = pluralizer.pluralize(bet)
    dur_str = (
        f"{duration_secs // 60} мин" if duration_secs < 3600
        else f"{duration_secs // 3600} ч"
    )

    dm_sent = False
    try:
        await bot.send_message(
            user_id,
            f"✏️ Ты создал Угадайку!\n"
            f"Ставка: <b>{bet} {bet_str}</b>  |  Время: <b>{dur_str}</b>\n\n"
            f"Отправь мне загадываемое слово:",
            parse_mode=ParseMode.HTML,
        )
        dm_sent = True
    except TelegramBadRequest:
        pass

    if dm_sent:
        await score_service.add_score(user_id, chat_id, -bet, admin_id=user_id)

    lobby_text = (
        f"🔤 <b>Угадайка!</b>\n\n"
        f"{user_mention} загадывает слово…\n"
        f"💰 Ставка: <b>{bet} {bet_str}</b>  |  ⏰ Время: <b>{dur_str}</b>\n\n"
        f"<i>Ожидаем слово от автора</i>"
    )
    kb = None if dm_sent else _open_dm_kb(bot_me.username)
    lobby_msg = await message.answer(lobby_text, parse_mode=ParseMode.HTML, reply_markup=kb)

    await store.wg_pending_set_lobby_msg(game_id, lobby_msg.message_id)
    schedule_delete(bot, message, delay=5)


# ── /rword <ставка> <время> — угадайка с рандомным словом ───────────────────

@router.message(Command("rword"))
@inject
async def cmd_random_wordgame(
    message: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    args = (message.text or "").split()[1:]
    wg = config.wordgame
    rwg = config.rwordgame

    if len(args) < 2:
        await reply_and_delete(
            message,
            f"Использование: <code>/rword &lt;ставка&gt; &lt;время&gt;</code>\n"
            f"Ставка: 0–{rwg.max_bet}  |  Время: от {wg.min_duration_seconds // 60}m "
            f"до {wg.max_duration_seconds // 60}m\n\n"
            f"Бот сам загадывает слово из {rwg.min_word_length}–{rwg.max_word_length} букв.\n"
            f"Пример: <code>/rword 20 10m</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not args[0].isdigit():
        await reply_and_delete(message, "❌ Ставка должна быть числом.")
        return
    bet = int(args[0])
    if not (0 <= bet <= rwg.max_bet):
        await reply_and_delete(message, f"❌ Ставка: от 0 до {rwg.max_bet}.")
        return

    duration_secs = _parse_duration_seconds(args[1])
    if duration_secs is None:
        await reply_and_delete(
            message,
            "❌ Неверный формат времени. Используй <code>Xm</code> или <code>Xh</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not (wg.min_duration_seconds <= duration_secs <= wg.max_duration_seconds):
        await reply_and_delete(
            message,
            f"❌ Время: от {wg.min_duration_seconds // 60}m "
            f"до {wg.max_duration_seconds // 60}m.",
        )
        return

    # Выбираем случайное слово из словаря
    word = pick_random_word(rwg.min_word_length, rwg.max_word_length)
    if word is None:
        await reply_and_delete(
            message,
            "❌ Не удалось подобрать слово. Сообщи об ошибке через /bug.",
        )
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    if bet > 0:
        score = await score_service.get_score(user_id, chat_id)
        if score.value < bet:
            await reply_and_delete(
                message,
                f"❌ Недостаточно баллов. У тебя {score.value}, нужно {bet}.",
            )
            return

    # Лимит на создание игр — общий с /word
    window_secs = wg.game_window_hours * 3600
    created = await store.wg_rate_check(user_id, wg.max_games_per_window, window_secs)
    if created >= wg.max_games_per_window:
        await reply_and_delete(
            message,
            f"❌ Лимит: {wg.max_games_per_window} игры за {wg.game_window_hours} ч. Попробуй позже.",
        )
        return

    await user_repo.upsert(User(
        id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    ))

    ends_at = time.time() + duration_secs
    game_id = str(random.randint(10000, 99999))

    # Ставка списывается сразу — создатель сам задаёт её «в банк»
    if bet > 0:
        await score_service.add_score(user_id, chat_id, -bet, admin_id=user_id)

    await store.wg_rate_record(user_id, window_secs)

    # Создаём объект игры с временным message_id=0, потом обновим
    game = WordGame(
        game_id=game_id,
        chat_id=chat_id,
        creator_id=user_id,
        word=word,
        bet=bet,
        ends_at=ends_at,
        is_random=True,
    )

    user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'
    bet_str = pluralizer.pluralize(bet)
    dur_str = (
        f"{duration_secs // 60} мин" if duration_secs < 3600
        else f"{duration_secs // 3600} ч"
    )

    # Заголовок: указываем, что слово загадал бот, а не человек
    game_header = (
        f"🎲 <b>Угадайка (случайное слово)</b>\n\n"
        f"{user_mention} запустил(а) угадайку!\n"
        f"Слово из <b>{len(word)}</b> букв: {game.masked}\n"
        f"Открыто: <b>{game.revealed_count}/{len(word)}</b>\n\n"
        f"💰 Ставка: <b>{bet} {bet_str}</b>  ⏰ До: <b>{{ends_str}}</b>\n\n"
        f"<i>Ответь на это сообщение словом, чтобы угадать</i>"
    )
    ends_dt = datetime.fromtimestamp(ends_at, tz=TZ_MSK)
    ends_str = ends_dt.strftime("%H:%M")
    game_text = game_header.replace("{ends_str}", ends_str)

    game_msg = await message.answer(game_text, parse_mode=ParseMode.HTML)
    game.message_id = game_msg.message_id

    await store.wg_game_create(game)
    await store.wg_chat_add(chat_id, game_id)

    try:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=game_msg.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pass

    schedule_delete(bot, message, delay=5)


# ── /start в ЛС ─────────────────────────────────────────────────────────────

@router.message(Command("start"), F.chat.type == "private")
@inject
async def cmd_start_private(
    message: Message,
    store: FromDishka[RedisStore],
) -> None:
    user_id = message.from_user.id
    game_id = await store.wg_awaiting_get(user_id)
    if game_id is None:
        await message.answer(
            "Привет! Чтобы начать игру, используй /word в групповом чате."
        )
        return

    pending = await store.wg_pending_get(game_id)
    if pending is None:
        await store.wg_awaiting_delete(user_id)
        await message.answer(
            "❌ Игра устарела (прошло больше 10 мин).\n"
            "Начни новую командой /word в группе."
        )
        return

    await message.answer(
        "✏️ Отправь мне загадываемое слово:\n\n"
        "• Только буквы (кириллица или латиница)\n"
        "• Без пробелов и цифр\n"
        "• Длина: не менее 2 символов"
    )


# ── Приём слова в ЛС ─────────────────────────────────────────────────────────

@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
@inject
async def msg_private_word(
    message: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    user_id = message.from_user.id
    game_id = await store.wg_awaiting_get(user_id)
    if game_id is None:
        await message.answer(
            "Нет активной игры, ожидающей слово.\n"
            "Начни командой /word в групповом чате."
        )
        return

    word = normalize_word(message.text or "")
    wg = config.wordgame

    if not is_valid_word(word, wg.min_word_length, wg.max_word_length):
        await message.answer(
            f"❌ Неверное слово. Только буквы, длина {wg.min_word_length}–{wg.max_word_length}.\n"
            "Попробуй ещё раз:"
        )
        return

    pending = await store.wg_pending_get(game_id)
    if pending is None:
        await store.wg_awaiting_delete(user_id)
        await message.answer(
            "❌ Игра устарела. Начни новую командой /word в группе."
        )
        return

    await store.wg_pending_delete(game_id)
    await store.wg_awaiting_delete(user_id)

    chat_id = pending["chat_id"]
    bet = pending["bet"]
    duration_secs = pending["duration_seconds"]
    lobby_msg_id = pending.get("lobby_msg_id", 0)
    ends_at = time.time() + duration_secs

    game = WordGame(
        game_id=game_id,
        chat_id=chat_id,
        creator_id=user_id,
        word=word,
        bet=bet,
        ends_at=ends_at,
        message_id=lobby_msg_id,
    )

    await store.wg_game_create(game)
    await store.wg_chat_add(chat_id, game_id)

    if lobby_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=lobby_msg_id,
                text=_game_text(game, pluralizer),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            await bot.pin_chat_message(chat_id=chat_id,
                                       message_id = lobby_msg_id,
                                       disable_notification=True)
        except TelegramBadRequest:
            pass

    await message.answer(
        f"✅ Слово принято! Игра запущена.\n"
        f"Игрокам нужно угадать слово из <b>{len(word)}</b> букв.",
        parse_mode=ParseMode.HTML,
    )


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
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Ищем игру по message_id сообщения, на которое ответили
    raw = await store.wg_game_by_message_id(chat_id, replied.message_id)
    if raw is None:
        # Это не игровое сообщение — игнорируем
        return

    game = _raw_to_game(raw)
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
                text=_game_text(game, pluralizer),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass

        await store.wg_game_save_raw(game.game_id, _game_to_raw(game))

        hint_parts = [
            f"❌ {user_mention}: «{guess}» — не то "
            f"({matched_count}/{len(game.word)} на месте)"
        ]

        if game.bet > 0 and cost > 0:
            await score_service.add_score(game.creator_id, chat_id, cost, admin_id=user_id)            
            await score_service.add_score(user_id, chat_id, -cost, admin_id=user_id)
            hint_parts.append(f"<i>−{cost} балл за попытку</i>")

        hint = await message.answer("\n".join(hint_parts), parse_mode=ParseMode.HTML)
        schedule_delete(bot, hint, delay=15)