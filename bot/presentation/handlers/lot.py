"""Хендлер /lot — аукцион для администраторов.

Флоу:
  1. /lot <время> <стартовая_цена> <описание…>
     Пример: /lot 10m 50 Кастомный тег «Король чата»
  2. Бот публикует лот: описание, текущая ставка, кнопки «+N»
  3. Участники нажимают кнопки — ставки списываются моментально,
     предыдущему лидеру деньги возвращаются
  4. По истечении таймера — победитель сохраняет лот, сообщение
     редактируется с итогом и удаляется через DELETE_DELAY секунд
  5. Если никто не поставил — лот снимается, сообщение удаляется

Конфиг (lot в config.yaml):
  enabled          — вкл/выкл
  min_duration_sec — минимальная длительность аукциона
  max_duration_sec — максимальная длительность
  min_start_price  — минимальная стартовая цена
  max_start_price  — максимальная стартовая цена
  bid_steps        — список шагов ставок [5, 10, 25, 50, 100]
  delete_delay     — секунд до удаления результата
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.score_service import ScoreService
from bot.domain.bot_utils import is_admin
from bot.domain.entities import User
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, schedule_delete, schedule_delete_id

logger = logging.getLogger(__name__)
router = Router(name="lot")

# ── Redis-ключи ─────────────────────────────────────────────────────────
_LOT_KEY    = "lot:game:{chat_id}:{lot_id}"   # данные лота
_ACTIVE_KEY = "lot:active:{chat_id}"           # → lot_id текущего лота в чате

_DURATION_RE = re.compile(r"^(\d+)(m|h)$", re.IGNORECASE)


def _lot_key(chat_id: int, lot_id: str) -> str:
    return _LOT_KEY.format(chat_id=chat_id, lot_id=lot_id)

def _active_key(chat_id: int) -> str:
    return _ACTIVE_KEY.format(chat_id=chat_id)

def _make_lot_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


# ── Парсинг длительности ─────────────────────────────────────────────────

def _parse_duration(token: str) -> int | None:
    """Парсит '10m', '2h' → секунды."""
    m = _DURATION_RE.match(token)
    if not m:
        return None
    val, unit = int(m.group(1)), m.group(2).lower()
    return val * 60 if unit == "m" else val * 3600


# ── Рендер текста лота ───────────────────────────────────────────────────

def _lot_text(data: dict, p: ScorePluralizer) -> str:
    ends_at = data["expires_at"]
    ends_str = datetime.fromtimestamp(ends_at, tz=TZ_MSK).strftime("%H:%M:%S")
    cur = data["current_price"]
    sw = p.pluralize(cur)

    leader_line = ""
    if data["leader_id"]:
        leader_line = f"\n👑 Лидер: {data['leader_display']} — <b>{cur} {sw}</b>"
    else:
        start = data["start_price"]
        sw_start = p.pluralize(start)
        leader_line = f"\n💰 Стартовая цена: <b>{start} {sw_start}</b>"

    bids_count = data["bids_count"]
    bids_line = f"🔢 Ставок: <b>{bids_count}</b>" if bids_count else "Ставок пока нет"

    return (
        f"🔨 <b>Аукцион!</b>\n\n"
        f"📦 <b>{data['description']}</b>"
        f"{leader_line}\n\n"
        f"{bids_line}\n"
        f"⏰ Завершение: <b>{ends_str}</b>\n\n"
        f"<i>Нажми кнопку, чтобы сделать ставку</i>"
    )


# ── Клавиатура с шагами ставок ────────────────────────────────────────────

def _bid_kb(lot_id: str, steps: list[int], p: ScorePluralizer) -> InlineKeyboardMarkup:
    row = []
    for step in steps:
        row.append(InlineKeyboardButton(
            text=f"+{step}",
            callback_data=f"lot:bid:{lot_id}:{step}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[row])


# ── /lot <время> <цена> <описание…> ──────────────────────────────────────

@router.message(Command("lot"))
@inject
async def cmd_lot(
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

    cfg = config.lot
    if not cfg.enabled:
        await reply_and_delete(message, "❌ Аукционы отключены.")
        return

    # Только конфиг-администраторы
    if not is_admin(message.from_user.username, config.admin.users):
        await reply_and_delete(message, "❌ Только администраторы могут выставлять лоты.")
        return

    args = (message.text or "").split(None, 3)[1:]  # [время, цена, описание]
    if len(args) < 3:
        await reply_and_delete(
            message,
            "🔨 <b>Аукцион</b>\n\n"
            "Использование: <code>/lot &lt;время&gt; &lt;стартовая_цена&gt; &lt;описание&gt;</code>\n"
            "Пример: <code>/lot 10m 50 Кастомный тег «Король»</code>\n\n"
            f"Время: {cfg.min_duration_sec // 60}м – {cfg.max_duration_sec // 3600}ч",
            parse_mode=ParseMode.HTML,
        )
        return

    # Парсим длительность
    duration_sec = _parse_duration(args[0])
    if duration_sec is None:
        await reply_and_delete(message, "❌ Неверный формат времени. Используй <code>Xm</code> или <code>Xh</code>.", parse_mode=ParseMode.HTML)
        return
    if not (cfg.min_duration_sec <= duration_sec <= cfg.max_duration_sec):
        await reply_and_delete(
            message,
            f"❌ Длительность: от {cfg.min_duration_sec // 60}м до {cfg.max_duration_sec // 3600}ч.",
        )
        return

    # Парсим стартовую цену
    try:
        start_price = int(args[1])
        if start_price < 0:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, "❌ Стартовая цена должна быть неотрицательным числом.")
        return
    if not (cfg.min_start_price <= start_price <= cfg.max_start_price):
        await reply_and_delete(
            message,
            f"❌ Стартовая цена: от {cfg.min_start_price} до {cfg.max_start_price}.",
        )
        return

    description = args[2].strip()
    if not description:
        await reply_and_delete(message, "❌ Описание лота не может быть пустым.")
        return
    if len(description) > 200:
        await reply_and_delete(message, "❌ Описание слишком длинное (макс. 200 символов).")
        return

    chat_id = message.chat.id

    # Проверяем активный лот в чате
    existing = await store._r.get(_active_key(chat_id))
    if existing:
        if await store._r.exists(_lot_key(chat_id, existing)):
            await reply_and_delete(message, "❌ В этом чате уже идёт аукцион. Дождитесь завершения.")
            return
        await store._r.delete(_active_key(chat_id))

    lot_id = _make_lot_id()
    expires_at = time.time() + duration_sec
    p = pluralizer

    data = {
        "lot_id": lot_id,
        "chat_id": chat_id,
        "creator_id": message.from_user.id,
        "description": description,
        "start_price": start_price,
        "current_price": start_price,
        "leader_id": None,
        "leader_display": "",
        "bids_count": 0,
        "message_id": 0,
        "expires_at": expires_at,
        "created_at": time.time(),
    }

    lot_text = _lot_text(data, p)
    sent = await message.answer(
        lot_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_bid_kb(lot_id, cfg.bid_steps, p),
    )

    data["message_id"] = sent.message_id
    ttl = duration_sec + 60

    pipe = store._r.pipeline()
    pipe.set(_lot_key(chat_id, lot_id), json.dumps(data, ensure_ascii=False), ex=ttl)
    pipe.set(_active_key(chat_id), lot_id, ex=ttl)
    await pipe.execute()

    # Пинаем сообщение
    try:
        await message.bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pass

    schedule_delete(message.bot, message, delay=5)
    logger.info("lot: создан лот %s в чате %d, цена %d, длит %d сек", lot_id, chat_id, start_price, duration_sec)


# ── Callback: ставка ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("lot:bid:"))
@inject
async def cb_lot_bid(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    score_repo: FromDishka[IScoreRepository],
    pluralizer: FromDishka[ScorePluralizer],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
) -> None:
    parts = cb.data.split(":")
    if len(parts) < 4:
        await cb.answer()
        return

    lot_id = parts[2]
    try:
        step = int(parts[3])
    except ValueError:
        await cb.answer()
        return

    chat_id = cb.message.chat.id
    user_id = cb.from_user.id
    key = _lot_key(chat_id, lot_id)

    # ── Mutex: один ход за раз ───────────────────────────────────────────
    lock_key = f"lot:lock:{chat_id}:{lot_id}"
    acquired = await store._r.set(lock_key, "1", nx=True, ex=5)
    if not acquired:
        await cb.answer("⏳ Подожди секунду…")
        return

    try:
        raw = await store._r.get(key)
        if raw is None:
            await cb.answer("⏰ Аукцион завершён.", show_alert=True)
            return

        data = json.loads(raw)

        if data["expires_at"] < time.time():
            await cb.answer("⏰ Аукцион завершён.", show_alert=True)
            return

        p = pluralizer
        cfg = config.lot

        # Валидация шага
        if step not in cfg.bid_steps:
            await cb.answer("❌ Недопустимый шаг ставки.", show_alert=True)
            return

        new_price = data["current_price"] + step

        # Upsert участника
        await user_repo.upsert(User(
            id=user_id,
            username=cb.from_user.username,
            full_name=cb.from_user.full_name,
        ))

        # Проверяем баланс
        bal = await score_service.get_score(user_id, chat_id)
        if bal.value < new_price:
            sw = p.pluralize(new_price)
            await cb.answer(
                f"❌ Недостаточно баллов. Нужно {new_price} {sw}, у тебя {bal.value}.",
                show_alert=True,
            )
            return

        prev_leader_id = data["leader_id"]
        prev_price = data["current_price"]

        # Возвращаем деньги предыдущему лидеру
        if prev_leader_id and prev_leader_id != user_id:
            await score_repo.add_delta(prev_leader_id, chat_id, prev_price)

        # Если ставящий уже лидер — возвращаем его предыдущую ставку
        if prev_leader_id == user_id:
            await score_repo.add_delta(user_id, chat_id, prev_price)

        # Списываем новую ставку
        result = await score_service.spend_score(
            actor_id=user_id,
            target_id=user_id,
            chat_id=chat_id,
            cost=new_price,
        )
        if not result.success:
            # Возможна гонка — вернуть ранее возвращённые деньги нельзя атомарно,
            # но lock минимизирует это. Просто сообщаем об ошибке.
            sw = p.pluralize(new_price)
            await cb.answer(
                f"❌ Недостаточно баллов. Нужно {new_price} {sw}.",
                show_alert=True,
            )
            return

        display = user_link(
            cb.from_user.username,
            cb.from_user.full_name or "",
            user_id,
        )

        data["current_price"] = new_price
        data["leader_id"] = user_id
        data["leader_display"] = display
        data["bids_count"] += 1

        ttl_left = max(30, int(data["expires_at"] - time.time()))
        await store._r.set(key, json.dumps(data, ensure_ascii=False), ex=ttl_left + 60)

        # Обновляем сообщение
        new_text = _lot_text(data, p)
        try:
            await cb.message.edit_text(
                new_text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
                reply_markup=_bid_kb(lot_id, cfg.bid_steps, p),
            )
        except TelegramBadRequest:
            pass

        sw_new = p.pluralize(new_price)
        await cb.answer(f"✅ Твоя ставка: {new_price} {sw_new}!")

    finally:
        await store._r.delete(lock_key)


# ── Публичная функция завершения (используется loop'ом) ───────────────────

async def finish_lot(
    bot,
    chat_id: int,
    lot_id: str,
    store: RedisStore,
    p: ScorePluralizer,
    delete_delay: int = 120,
) -> None:
    """Завершает аукцион: редактирует сообщение с итогом, анпинит, планирует удаление."""
    key = _lot_key(chat_id, lot_id)
    raw = await store._r.get(key)
    if raw is None:
        return

    # Атомарно удаляем
    if not await store._r.delete(key):
        return
    await store._r.delete(_active_key(chat_id))

    data = json.loads(raw)
    msg_id = data.get("message_id", 0)

    # Анпиним
    if msg_id:
        try:
            await bot.unpin_chat_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

    winner_id = data["leader_id"]
    description = data["description"]

    if winner_id:
        winner_display = data["leader_display"]
        prize = data["current_price"]
        sw = p.pluralize(prize)
        result_text = (
            f"🏆 <b>Аукцион завершён!</b>\n\n"
            f"📦 <b>{description}</b>\n\n"
            f"Победитель: {winner_display}\n"
            f"Финальная ставка: <b>{prize} {sw}</b>\n\n"
            f"Поздравляем! 🎉"
        )
        logger.info("lot: лот %s в чате %d выиграл user %d за %d", lot_id, chat_id, winner_id, prize)
    else:
        result_text = (
            f"🔨 <b>Аукцион завершён</b>\n\n"
            f"📦 <b>{description}</b>\n\n"
            f"<i>Никто не сделал ставку. Лот снят.</i>"
        )
        logger.info("lot: лот %s в чате %d завершён без ставок", lot_id, chat_id)

    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=result_text,
                parse_mode="HTML",
                reply_markup=None,
            )
            schedule_delete_id(bot, chat_id, msg_id, delay=delete_delay)
        except TelegramBadRequest:
            pass
        except Exception:
            logger.exception("lot: не удалось отредактировать итог лота %s", lot_id)
