"""Хендлер /anagram — угадай слово по перемешанным буквам.

Флоу:
1. /anagram <ставка>  — бот публикует перемешанные буквы, приз = ставка
                        (списывается с баланса бота).
2. Любой участник делает реплай на сообщение бота → попытка угадать.
3. Неверная попытка стоит attempt_cost баллов (списывается с игрока).
4. Первый, кто угадал, забирает весь банк.
5. Если время вышло (timeout) — игра закрывается, приз сгорает.
"""

from __future__ import annotations

import json
import logging
import random
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.infrastructure.word_loader import pick_random_word
from bot.presentation.utils import check_gameban, reply_and_delete, schedule_delete, schedule_delete_id

logger = logging.getLogger(__name__)
router = Router(name="anagram")

# ── Redis-ключи ─────────────────────────────────────────────────────────
_GAME_KEY       = "anagram:game:{game_id}"
_MSG_KEY        = "anagram:msg:{chat_id}:{message_id}"   # → game_id
_ACTIVE_KEY     = "anagram:active:{chat_id}"              # → game_id
_CHATS_KEY      = "anagram:chats"                         # sorted set chat_id → last_ts
_NEXT_AUTO_KEY  = "anagram:next_auto:{chat_id}"           # → float timestamp


def _game_key(game_id: str) -> str:
    return _GAME_KEY.format(game_id=game_id)

def _msg_key(chat_id: int, message_id: int) -> str:
    return _MSG_KEY.format(chat_id=chat_id, message_id=message_id)

def _active_key(chat_id: int) -> str:
    return _ACTIVE_KEY.format(chat_id=chat_id)

def _next_auto_key(chat_id: int) -> str:
    return _NEXT_AUTO_KEY.format(chat_id=chat_id)


def _make_game_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


def _shuffle_word(word: str) -> str:
    """Перемешивает буквы слова так, чтобы результат гарантированно отличался от оригинала."""
    chars = list(word)
    if len(chars) <= 1:
        return word
    for _ in range(20):
        random.shuffle(chars)
        result = "".join(chars)
        if result != word:
            return result
    return "".join(chars)


def _game_text(shuffled: str, bet: int, tries_count: int, sw: str, ends_at: float) -> str:
    from datetime import datetime
    from bot.domain.tz import TZ_MSK
    ends_str = datetime.fromtimestamp(ends_at, tz=TZ_MSK).strftime("%H:%M:%S")
    return (
        f"🔤 <b>Анаграмма!</b>\n\n"
        f"Угадай слово: <b>{shuffled}</b>\n\n"
        f"💰 Приз: <b>{bet} {sw}</b>\n"
        f"🎯 Попыток: <b>{tries_count}</b>\n"
        f"⏰ До: <b>{ends_str}</b>\n\n"
        f"<i>Реплай на это сообщение с ответом</i>"
    )


# ── /anagram <ставка> ────────────────────────────────────────────────────

@router.message(Command("anagram"))
@inject
async def cmd_anagram(
    message: Message,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None or message.bot is None:
        return

    ban_msg = await check_gameban(store, message.from_user.id, message.chat.id, formatter._t)
    if ban_msg:
        await reply_and_delete(message, ban_msg)
        return

    cfg = config.anagram
    if not cfg.enabled:
        await reply_and_delete(message, "❌ Анаграммы отключены.")
        return

    args = (message.text or "").split()[1:]
    p = pluralizer
    chat_id = message.chat.id

    if not args:
        sw = p.pluralize(cfg.max_bet)
        await reply_and_delete(
            message,
            f"🔤 <b>Анаграмма</b>\n\n"
            f"Использование: <code>/anagram &lt;ставка&gt;</code>\n"
            f"Ставка: {cfg.min_bet}–{cfg.max_bet} {sw}\n\n"
            f"Бот загадывает слово из {cfg.min_word_length}–{cfg.max_word_length} букв.\n"
            f"Неверная попытка стоит <b>{cfg.attempt_cost}</b> балл.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        bet = int(args[0])
        if bet <= 0:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, "❌ Ставка должна быть положительным числом.")
        return

    if bet < cfg.min_bet:
        sw = p.pluralize(cfg.min_bet)
        await reply_and_delete(message, f"❌ Минимальная ставка: {cfg.min_bet} {sw}.")
        return
    if bet > cfg.max_bet:
        sw = p.pluralize(cfg.max_bet)
        await reply_and_delete(message, f"❌ Максимальная ставка: {cfg.max_bet} {sw}.")
        return

    # Проверяем, нет ли уже активной игры в чате
    existing_id = await store._r.get(_active_key(chat_id))
    if existing_id:
        existing_raw = await store._r.get(_game_key(existing_id))
        if existing_raw:
            await reply_and_delete(
                message,
                "❌ В этом чате уже есть активная анаграмма. Дождитесь завершения!",
            )
            return
        # Ключ игры протух, но active не почистился — подчищаем
        await store._r.delete(_active_key(chat_id))

    # Выбираем слово из словаря
    word = pick_random_word(cfg.min_word_length, cfg.max_word_length)
    if word is None:
        await reply_and_delete(
            message,
            "❌ Не удалось подобрать слово. Попробуй позже.",
        )
        return

    user_id = message.from_user.id

    # Upsert пользователя
    await user_repo.upsert(User(
        id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    ))

    # Проверяем баланс бота и списываем ставку с него
    bot_balance = await score_service.get_bot_balance(message.bot.id, chat_id)
    if bot_balance < bet:
        await reply_and_delete(
            message,
            f"❌ У бота недостаточно баллов для приза ({bot_balance} < {bet}). "
            f"Попроси администратора пополнить баланс.",
        )
        return

    await score_service.add_score(message.bot.id, chat_id, -bet, admin_id=user_id)

    # Создаём игру
    game_id = _make_game_id()
    shuffled = _shuffle_word(word)
    expires_at = time.time() + cfg.answer_timeout_seconds

    data = {
        "game_id": game_id,
        "chat_id": chat_id,
        "creator_id": user_id,
        "word": word,
        "shuffled": shuffled,
        "bet": bet,
        "tries": [],
        "message_id": 0,
        "created_at": time.time(),
        "expires_at": expires_at,
        "is_auto": False,
    }

    sw_bet = p.pluralize(bet)
    game_text = _game_text(shuffled, bet, 0, sw_bet, expires_at)
    sent = await message.answer(game_text, parse_mode=ParseMode.HTML)

    data["message_id"] = sent.message_id
    ttl = cfg.answer_timeout_seconds + 30

    pipe = store._r.pipeline()
    pipe.set(_game_key(game_id), json.dumps(data), ex=ttl)
    pipe.set(_msg_key(chat_id, sent.message_id), game_id, ex=ttl)
    pipe.set(_active_key(chat_id), game_id, ex=ttl)
    pipe.zadd(_CHATS_KEY, {str(chat_id): time.time()})
    await pipe.execute()

    try:
        await message.bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pass

    # Удаляем исходную команду
    schedule_delete(sent.bot, message, delay=5)


# ── Публичная утилита для создания игры (используется loop'ом) ───────────

async def create_anagram_game(
    bot,
    chat_id: int,
    bet: int,
    store: RedisStore,
    score_service: ScoreService,
    pluralizer: ScorePluralizer,
    cfg,
) -> bool:
    """Создать и опубликовать анаграмму в указанном чате.

    Возвращает True если успешно, False если не удалось (нет баланса, нет слова, уже активна).
    """
    # Проверяем активную игру
    existing_id = await store._r.get(_active_key(chat_id))
    if existing_id:
        existing_raw = await store._r.get(_game_key(existing_id))
        if existing_raw:
            return False
        await store._r.delete(_active_key(chat_id))

    word = pick_random_word(cfg.min_word_length, cfg.max_word_length)
    if word is None:
        logger.warning("anagram_loop: не удалось подобрать слово для чата %d", chat_id)
        return False

    bot_balance = await score_service.get_bot_balance(bot.id, chat_id)
    if bot_balance < bet:
        logger.warning("anagram_loop: у бота нет баллов в чате %d (%d < %d)", chat_id, bot_balance, bet)
        return False

    await score_service.add_score(bot.id, chat_id, -bet, admin_id=bot.id)

    game_id = _make_game_id()
    shuffled = _shuffle_word(word)
    expires_at = time.time() + cfg.answer_timeout_seconds
    sw_bet = pluralizer.pluralize(bet)
    game_text = _game_text(shuffled, bet, 0, sw_bet, expires_at)

    try:
        sent = await bot.send_message(chat_id, game_text, parse_mode="HTML")
    except Exception as e:
        # Не смогли отправить — возвращаем деньги боту
        await score_service.add_score(bot.id, chat_id, bet, admin_id=bot.id)
        logger.error("anagram_loop: не удалось отправить сообщение в чат %d: %s", chat_id, e)
        return False

    data = {
        "game_id": game_id,
        "chat_id": chat_id,
        "creator_id": bot.id,
        "word": word,
        "shuffled": shuffled,
        "bet": bet,
        "tries": [],
        "message_id": sent.message_id,
        "created_at": time.time(),
        "expires_at": expires_at,
        "is_auto": True,
    }

    ttl = cfg.answer_timeout_seconds + 30
    pipe = store._r.pipeline()
    pipe.set(_game_key(game_id), json.dumps(data), ex=ttl)
    pipe.set(_msg_key(chat_id, sent.message_id), game_id, ex=ttl)
    pipe.set(_active_key(chat_id), game_id, ex=ttl)
    pipe.zadd(_CHATS_KEY, {str(chat_id): time.time()})
    await pipe.execute()

    try:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pass

    logger.info(
        "anagram_loop: создана авто-игра %s в чате %d, слово длиной %d, ставка %d",
        game_id, chat_id, len(word), bet,
    )
    return True


# ── Reply — попытка угадать ──────────────────────────────────────────────

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
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
    pluralizer: FromDishka[ScorePluralizer],
) -> None:
    if message.from_user is None or message.bot is None:
        return

    cfg = config.anagram
    if not cfg.enabled:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    # Ищем игру по message_id сообщения, на которое ответили
    game_id = await store._r.get(_msg_key(chat_id, replied.message_id))
    if game_id is None:
        # Не наша игра — передаём следующему хендлеру
        raise SkipHandler

    raw = await store._r.get(_game_key(game_id))
    if raw is None:
        return

    data = json.loads(raw)

    # Проверка таймаута
    if data["expires_at"] < time.time():
        return

    p = pluralizer
    guess = (message.text or "").strip().upper()

    # Минимальная валидация — только буквы нужной длины
    if not guess.isalpha() or len(guess) != len(data["word"]):
        err = await message.reply(
            f"❌ Ответ должен быть словом из <b>{len(data['word'])}</b> букв.",
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(message.bot, err, message, delay=15)
        return

    user_mention = user_link(
        message.from_user.username,
        message.from_user.full_name or "",
        user_id,
    )

    # Проверяем повторную попытку с тем же словом
    already_tried = any(
        t["user_id"] == user_id and t["word"] == guess
        for t in data["tries"]
    )
    if already_tried:
        err = await message.reply(
            f"🔄 {user_mention}: «<b>{guess}</b>» ты уже пробовал. Другое слово!",
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(message.bot, err, message, delay=15)
        return

    # Upsert пользователя
    await user_repo.upsert(User(
        id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    ))

    schedule_delete(message.bot, message, delay=30)

    if guess == data["word"]:
        # ── ПОБЕДА ──────────────────────────────────────────────────
        # Атомарно финишируем
        deleted = await store._r.delete(_game_key(game_id))
        if not deleted:
            # Гонка — кто-то угадал раньше
            return

        await store._r.delete(_active_key(chat_id))
        await store._r.delete(_msg_key(chat_id, data["message_id"]))

        bet = data["bet"]
        await score_service.add_score(user_id, chat_id, bet, admin_id=user_id)

        sw_bet = p.pluralize(bet)
        tries_total = len(data["tries"]) + 1

        win_text = (
            f"🎉 <b>Анаграмма разгадана!</b>\n\n"
            f"Слово: <b>{data['word']}</b>\n"
            f"Угадал(а): {user_mention}\n"
            f"Попыток: <b>{tries_total}</b>\n"
            f"Приз: <b>+{bet} {sw_bet}</b> 🏆"
        )

        try:
            await message.bot.unpin_chat_message(
                chat_id=chat_id, message_id=data["message_id"]
            )
        except Exception:
            pass
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=data["message_id"],
                text=win_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            result = await message.bot.send_message(chat_id, win_text, parse_mode="HTML")
            schedule_delete(message.bot, result, delay=60)
            return

        schedule_delete_id(message.bot, chat_id, data["message_id"], delay=60)

    else:
        # ── НЕВЕРНО ──────────────────────────────────────────────────
        data["tries"].append({"user_id": user_id, "word": guess})
        ttl_left = max(30, int(data["expires_at"] - time.time()))

        # Снимаем балл за неверную попытку
        cost = cfg.attempt_cost
        if cost > 0:
            bal = await score_service.get_score(user_id, chat_id)
            if bal.value >= cost:
                await score_service.add_score(user_id, chat_id, -cost, admin_id=user_id)
                # Возвращаем на баланс бота
                await score_service.add_score(message.bot.id, chat_id, cost, admin_id=message.bot.id)

        # Обновляем игровое сообщение
        sw_bet = p.pluralize(data["bet"])
        new_text = _game_text(
            data["shuffled"], data["bet"],
            len(data["tries"]), sw_bet, data["expires_at"],
        )
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=data["message_id"],
                text=new_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        # Сохраняем обновлённые данные
        await store._r.set(_game_key(game_id), json.dumps(data), ex=ttl_left)

        cost_note = f"\n<i>−{cost} балл за попытку</i>" if cost > 0 else ""
        hint = await message.reply(
            f"❌ {user_mention}: «<b>{guess}</b>» — не то!{cost_note}",
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(message.bot, hint, delay=15)
