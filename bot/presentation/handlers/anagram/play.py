"""Reply-хендлер — попытка угадать слово."""

from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ParseMode
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import schedule_delete, schedule_delete_id

from bot.presentation.handlers.anagram.helpers import _game_text

router = Router(name="anagram_play")


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
    game_id = await store.anagram_msg_get(chat_id, replied.message_id)
    if game_id is None:
        # Не наша игра — передаём следующему хендлеру
        raise SkipHandler

    data = await store.anagram_game_get(game_id)
    if data is None:
        return

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
        if not await store.anagram_finish_win(game_id, chat_id, data["message_id"]):
            # Гонка — кто-то угадал раньше
            return

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
        await store.anagram_game_save(game_id, data, ttl_left)

        cost_note = f"\n<i>−{cost} балл за попытку</i>" if cost > 0 else ""
        hint = await message.reply(
            f"❌ {user_mention}: «<b>{guess}</b>» — не то!{cost_note}",
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(message.bot, hint, delay=15)
