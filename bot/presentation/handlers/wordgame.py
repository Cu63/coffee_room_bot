"""Обработчики игры «Угадайка».

Флоу:
1. /wordgame <ставка> <время>  — в группе: создаёт игру, бот пишет создателю в ЛС
2. /start (в ЛС, без аргументов) — бот предлагает написать слово, если есть pending
3. Текст в ЛС — создатель отправляет загаданное слово
4. /guess <слово> — в группе: попытка угадать, бот редактирует игровое сообщение
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.wordgame_entities import (
    WordGame,
    compare,
    format_masked,
    is_valid_word,
    merge_revealed,
    normalize_word,
)
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, schedule_delete, schedule_delete_id

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
    """Кнопка «Написать слово в ЛС» — просто открывает бота без payload."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✏️ Написать слово в ЛС",
                url=f"https://t.me/{bot_username}",
            )
        ]]
    )


def _game_text(game: WordGame, pluralizer: ScorePluralizer) -> str:
    """Текст игрового сообщения с текущей маской слова."""
    ends_dt = datetime.fromtimestamp(game.ends_at, tz=TZ_MSK)
    ends_str = ends_dt.strftime("%H:%M")
    bet_str = pluralizer.pluralize(game.bet)
    return (
        f"🔤 <b>Угадайка!</b>\n\n"
        f"Слово из <b>{len(game.word)}</b> букв: {game.masked}\n"
        f"Открыто: <b>{game.revealed_count}/{len(game.word)}</b>\n\n"
        f"💰 Ставка: <b>{game.bet} {bet_str}</b>  ⏰ До: <b>{ends_str}</b>\n\n"
        f"<i>/guess &lt;слово&gt; — попробовать угадать</i>"
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
    }


# ── /wordgame <ставка> <время> ───────────────────────────────────────────────

@router.message(Command("wordgame"))
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
            f"Использование: <code>/wordgame &lt;ставка&gt; &lt;время&gt;</code>\n"
            f"Ставка: {wg.min_bet}–{wg.max_bet}  |  Время: от 3m до 1h\n\n"
            f"Пример: <code>/wordgame 50 10m</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not args[0].isdigit():
        await reply_and_delete(message, "❌ Ставка должна быть числом.")
        return
    bet = int(args[0])
    if not (wg.min_bet <= bet <= wg.max_bet):
        await reply_and_delete(
            message, f"❌ Ставка: от {wg.min_bet} до {wg.max_bet}."
        )
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

    # Списываем ставку сразу — призовой фонд
    await score_service.add_score(user_id, chat_id, -bet, admin_id=user_id)

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
    # Запоминаем что ждём слово от создателя
    await store.wg_awaiting_set(user_id, game_id)

    user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'
    bet_str = pluralizer.pluralize(bet)
    dur_str = (
        f"{duration_secs // 60} мин" if duration_secs < 3600
        else f"{duration_secs // 3600} ч"
    )

    # Пробуем сразу написать создателю в ЛС
    dm_sent = False
    try:
        await bot.send_message(
            user_id,
            f"✏️ Ты создал Угадайку в чате!\n"
            f"Ставка: <b>{bet} {bet_str}</b>  |  Время: <b>{dur_str}</b>\n\n"
            f"Отправь мне загадываемое слово:",
            parse_mode=ParseMode.HTML,
        )
        dm_sent = True
    except TelegramBadRequest:
        pass  # пользователь не начинал диалог с ботом

    # Публикуем лобби-сообщение в группе
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


# ── /start в ЛС — проверяем pending игру ────────────────────────────────────

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
            "Привет! Чтобы начать игру, используй команду /wordgame в групповом чате."
        )
        return

    pending = await store.wg_pending_get(game_id)
    if pending is None:
        await store.wg_awaiting_delete(user_id)
        await message.answer(
            "❌ Игра устарела (прошло больше 10 мин).\n"
            "Начни новую командой /wordgame в группе."
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
            "Начни командой /wordgame в групповом чате."
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
            "❌ Игра устарела (прошло больше 10 мин).\n"
            "Начни новую командой /wordgame в группе."
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

    # Редактируем лобби-сообщение — показываем маску
    if lobby_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=lobby_msg_id,
                text=_game_text(game, pluralizer),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass

    await message.answer(
        f"✅ Слово принято! Игра запущена.\n"
        f"Игрокам нужно угадать слово из <b>{len(word)}</b> букв.",
        parse_mode=ParseMode.HTML,
    )


# ── /guess <слово> ───────────────────────────────────────────────────────────

@router.message(Command("guess"))
@inject
async def cmd_guess(
    message: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    args = (message.text or "").split()[1:]
    if not args:
        await reply_and_delete(
            message,
            "Использование: <code>/guess &lt;слово&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    guess = normalize_word(args[0])

    await user_repo.upsert(User(
        id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    ))

    game_ids = await store.wg_chat_games(chat_id)
    if not game_ids:
        await reply_and_delete(message, "🤷 Нет активных игр в этом чате.")
        return

    wg = config.wordgame
    user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'
    tried_any = False

    for game_id in list(game_ids):
        raw = await store.wg_game_get(game_id)
        if raw is None:
            await store.wg_chat_remove(chat_id, game_id)
            continue

        game = _raw_to_game(raw)
        if game.finished or game.is_expired:
            continue
        if game.creator_id == user_id:
            continue  # нельзя угадывать свою игру
        if len(guess) != len(game.word):
            continue

        tried_any = True

        # Повтор попытки
        if game.already_tried(user_id, guess):
            err = await message.reply(
                f"🔄 «<b>{guess}</b>» ты уже пробовал. Другое слово!",
                parse_mode=ParseMode.HTML,
            )
            schedule_delete(bot, err, message, delay=10)
            return

        matches = compare(game.word, guess)
        new_revealed = merge_revealed(game.revealed, matches)
        is_correct = all(matches)
        matched_count = sum(matches)

        game.guesses.append({"user_id": user_id, "word": guess})
        game.revealed = new_revealed

        # Удаляем команду сразу
        schedule_delete(bot, message, delay=3)

        if is_correct:
            # ── ПОБЕДА ──────────────────────────────────────────────
            game.finished = True
            game.winner_id = user_id
            await store.wg_game_finish(game_id)
            await store.wg_chat_remove(chat_id, game_id)

            await score_service.add_score(user_id, chat_id, game.bet, admin_id=user_id)

            bet_str = pluralizer.pluralize(game.bet)
            win_text = (
                f"🎉 <b>Угадайка завершена!</b>\n\n"
                f"Слово: <b>{game.word}</b>\n"
                f"Угадал(а): {user_mention}\n"
                f"Приз: <b>+{game.bet} {bet_str}</b> 🏆"
            )

            # Редактируем игровое сообщение
            if game.message_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=game.message_id,
                        text=win_text,
                        parse_mode=ParseMode.HTML,
                    )
                    schedule_delete_id(bot, chat_id, game.message_id, delay=30)
                except TelegramBadRequest:
                    result_msg = await bot.send_message(
                        chat_id, win_text, parse_mode=ParseMode.HTML
                    )
                    schedule_delete(bot, result_msg, delay=30)
            else:
                result_msg = await bot.send_message(
                    chat_id, win_text, parse_mode=ParseMode.HTML
                )
                schedule_delete(bot, result_msg, delay=30)

        else:
            # ── НЕВЕРНО ──────────────────────────────────────────────
            # Редактируем игровое сообщение — открываем угаданные буквы
            if game.message_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=game.message_id,
                        text=_game_text(game, pluralizer),
                        parse_mode=ParseMode.HTML,
                    )
                except TelegramBadRequest:
                    pass

            # Сохраняем обновлённое состояние
            await store.wg_game_save_raw(game_id, _game_to_raw(game))

            # Краткий ответ с результатом попытки
            cost = wg.attempt_cost
            cost_deducted = False
            if cost > 0:
                bal = await score_service.get_score(user_id, chat_id)
                if bal.value >= cost:
                    await score_service.add_score(
                        user_id, chat_id, -cost, admin_id=user_id
                    )
                    cost_deducted = True

            hint_lines = [
                f"❌ {user_mention}: «{guess}» — не то "
                f"({matched_count}/{len(game.word)} на месте)"
            ]
            if cost_deducted:
                hint_lines.append(f"<i>−{cost} балл за попытку</i>")

            hint = await message.answer(
                "\n".join(hint_lines),
                parse_mode=ParseMode.HTML,
            )
            schedule_delete(bot, hint, delay=15)

        return  # обработали одну игру, выходим

    if not tried_any:
        await reply_and_delete(
            message,
            "🤷 Нет подходящих игр: длина слова не совпадает, "
            "либо ты создатель всех активных игр.",
        )