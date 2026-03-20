"""Команды /word и /rword — создание игры «Угадайка»."""

from __future__ import annotations

import random
import time
from datetime import datetime

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.bot_utils import parse_duration
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import TZ_MSK
from bot.domain.wordgame_entities import WordGame
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.redis_store import RedisStore
from bot.infrastructure.word_loader import pick_random_word
from bot.presentation.handlers.wordgame.helpers import game_text, open_dm_kb
from bot.presentation.utils import check_gameban, reply_and_delete, schedule_delete

router = Router(name="wordgame_start")


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
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None:
        return

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, message.from_user.id, message.chat.id, formatter._t)
    if ban_msg:
        await reply_and_delete(message, ban_msg)
        return

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

    duration_secs = parse_duration(args[1])
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
    kb = None if dm_sent else open_dm_kb(bot_me.username)
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
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None:
        return

    # Проверка самозапрета на игры
    ban_msg = await check_gameban(store, message.from_user.id, message.chat.id, formatter._t)
    if ban_msg:
        await reply_and_delete(message, ban_msg)
        return

    args = (message.text or "").split()[1:]
    wg = config.wordgame
    rwg = config.rwordgame

    # Проверка кулдауна между играми /rword
    cd_remaining = await store.wg_rword_cooldown_active(message.chat.id)
    if cd_remaining is not None:
        mins = cd_remaining // 60
        secs = cd_remaining % 60
        if mins > 0:
            cd_str = f"{mins} мин {secs} сек" if secs else f"{mins} мин"
        else:
            cd_str = f"{secs} сек"
        await reply_and_delete(
            message,
            f"⏳ Подожди ещё <b>{cd_str}</b> перед следующей игрой /rword.",
            parse_mode=ParseMode.HTML,
        )
        return

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

    duration_secs = parse_duration(args[1])
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

    # Лимит на создание игр — отдельный от /word
    window_secs = rwg.game_window_hours * 3600
    created = await store.wg_rate_check_rword(user_id, rwg.max_games_per_window, window_secs)
    if created >= rwg.max_games_per_window:
        await reply_and_delete(
            message,
            f"❌ Лимит: {rwg.max_games_per_window} игры за {rwg.game_window_hours} ч. Попробуй позже.",
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
        await score_service.add_score(bot.id, chat_id, -bet, admin_id=user_id)

    await store.wg_rate_record_rword(user_id, window_secs)

    # Устанавливаем кулдаун между играми /rword
    await store.wg_rword_cooldown_set(chat_id, rwg.cooldown_minutes * 60)

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
