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

import html
import logging
import random
import time
from datetime import datetime

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
from bot.domain.bot_utils import is_admin, parse_duration
from bot.domain.entities import User
from bot.domain.ledger import LedgerKind
from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import TZ_MSK
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.errors import AbortUpdate
from bot.presentation.utils import (
    NO_PREVIEW,
    callback_message,
    reply_and_delete,
    schedule_delete,
    schedule_delete_id,
)

logger = logging.getLogger(__name__)
router = Router(name="lot")

_DESCRIPTION_LIMIT = 200  # символов в описании лота


def _make_lot_id() -> str:
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"


# ── Рендер текста лота ───────────────────────────────────────────────────


def _lot_text(data: dict, p: ScorePluralizer, t: dict[str, str]) -> str:
    ends_str = datetime.fromtimestamp(data["expires_at"], tz=TZ_MSK).strftime("%H:%M:%S")
    if data["leader_id"]:
        cur = data["current_price"]
        leader_line = t["lot_leader"].format(leader=data["leader_display"], price=cur, score_word=p.pluralize(cur))
    else:
        start = data["start_price"]
        leader_line = t["lot_start_price"].format(price=start, score_word=p.pluralize(start))
    bids_count = data["bids_count"]
    bids_line = t["lot_bids"].format(count=bids_count) if bids_count else t["lot_no_bids"]
    # Описание пишет админ: экранируем, иначе «<3» ломает разметку и лот не публикуется
    return t["lot_text"].format(
        description=html.escape(data["description"]), leader_line=leader_line, bids_line=bids_line, ends=ends_str
    )


# ── Клавиатура с шагами ставок ────────────────────────────────────────────


def _bid_kb(lot_id: str, steps: list[int], p: ScorePluralizer) -> InlineKeyboardMarkup:
    row = []
    for step in steps:
        row.append(
            InlineKeyboardButton(
                text=f"+{step}",
                callback_data=f"lot:bid:{lot_id}:{step}",
            )
        )
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

    t = formatter._t
    cfg = config.lot
    if not cfg.enabled:
        await reply_and_delete(message, t["lot_disabled"])
        return

    # Только конфиг-администраторы
    if not is_admin(message.from_user, config.admin.users):
        await reply_and_delete(message, t["lot_admins_only"])
        return

    duration_range = {"min_minutes": cfg.min_duration_sec // 60, "max_hours": cfg.max_duration_sec // 3600}
    args = (message.text or "").split(None, 3)[1:]  # [время, цена, описание]
    if len(args) < 3:
        await reply_and_delete(message, t["lot_usage"].format(**duration_range), parse_mode=ParseMode.HTML)
        return

    # Парсим длительность
    duration_sec = parse_duration(args[0])
    if duration_sec is None:
        await reply_and_delete(message, t["lot_bad_duration"], parse_mode=ParseMode.HTML)
        return
    if not (cfg.min_duration_sec <= duration_sec <= cfg.max_duration_sec):
        await reply_and_delete(message, t["lot_duration_range"].format(**duration_range))
        return

    # Парсим стартовую цену
    try:
        start_price = int(args[1])
        if start_price < 0:
            raise ValueError
    except ValueError:
        await reply_and_delete(message, t["lot_bad_price"])
        return
    if not (cfg.min_start_price <= start_price <= cfg.max_start_price):
        await reply_and_delete(
            message, t["lot_price_range"].format(min_price=cfg.min_start_price, max_price=cfg.max_start_price)
        )
        return

    description = args[2].strip()
    if not description:
        await reply_and_delete(message, t["lot_empty_description"])
        return
    if len(description) > _DESCRIPTION_LIMIT:
        await reply_and_delete(message, t["lot_long_description"].format(limit=_DESCRIPTION_LIMIT))
        return

    chat_id = message.chat.id

    # Проверяем активный лот в чате
    existing = await store.lot_active_get(chat_id)
    if existing:
        if await store.lot_game_exists(chat_id, existing):
            await reply_and_delete(message, t["lot_already_running"])
            return
        await store.lot_active_delete(chat_id)

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

    lot_text = _lot_text(data, p, t)
    sent = await message.answer(
        lot_text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=_bid_kb(lot_id, cfg.bid_steps, p),
    )

    data["message_id"] = sent.message_id
    ttl = duration_sec + 60

    await store.lot_create(chat_id, lot_id, data, ttl)

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
    formatter: FromDishka[MessageFormatter],
) -> None:
    t = formatter._t
    parts = (cb.data or "").split(":")
    message = callback_message(cb)
    bot = cb.bot
    if len(parts) < 4 or message is None or bot is None:
        await cb.answer()
        return

    lot_id = parts[2]
    try:
        step = int(parts[3])
    except ValueError:
        await cb.answer()
        return

    chat_id = message.chat.id
    user_id = cb.from_user.id

    # ── Mutex: один ход за раз ───────────────────────────────────────────
    acquired = await store.lot_lock_acquire(chat_id, lot_id)
    if not acquired:
        await cb.answer(t["lot_wait"])
        return

    try:
        data = await store.lot_game_get(chat_id, lot_id)
        if data is None or data["expires_at"] < time.time():
            await cb.answer(t["lot_finished"], show_alert=True)
            return

        p = pluralizer
        cfg = config.lot

        # Валидация шага
        if step not in cfg.bid_steps:
            await cb.answer(t["lot_bad_step"], show_alert=True)
            return

        new_price = data["current_price"] + step
        prev_leader_id = data["leader_id"]
        prev_price = data["current_price"]

        # Upsert участника
        await user_repo.upsert(
            User(
                id=user_id,
                username=cb.from_user.username,
                full_name=cb.from_user.full_name,
            )
        )

        # Лидер, повышающий свою ставку, доплачивает только разницу:
        # prev_price уже лежит у бота.
        is_self_raise = prev_leader_id == user_id
        charge = new_price - prev_price if is_self_raise else new_price

        # Порядок важен: сначала СПИСЫВАЕМ с нового участника (атомарно),
        # потом ВОЗВРАЩАЕМ предыдущему лидеру.
        result = await score_service.spend_score(
            actor_id=user_id,
            target_id=user_id,
            chat_id=chat_id,
            cost=charge,
            kind=LedgerKind.STAKE,
            ref=f"lot:{lot_id}",
            bot_id=bot.id,
        )
        if not result.success:
            await cb.answer(
                t["lot_not_enough"].format(
                    charge=charge, score_word=p.pluralize(charge), balance=result.current_balance
                ),
                show_alert=True,
            )
            return

        # Бот отдаёт ставку перебитому лидеру
        if prev_leader_id and not is_self_raise:
            ref = f"lot:{lot_id}"
            await score_repo.add_delta(
                prev_leader_id, chat_id, prev_price, kind=LedgerKind.REFUND, ref=ref, counterparty_id=bot.id
            )
            await score_repo.add_delta(
                bot.id, chat_id, -prev_price, kind=LedgerKind.REFUND, ref=ref, counterparty_id=prev_leader_id
            )

        display = user_link(
            cb.from_user.username,
            cb.from_user.full_name or "",
            user_id,
        )

        expected_bids = data["bids_count"]
        data["current_price"] = new_price
        data["leader_id"] = user_id
        data["leader_display"] = display
        data["bids_count"] += 1

        ttl_left = max(30, int(data["expires_at"] - time.time()))
        saved = await store.lot_game_save_if_unchanged(chat_id, lot_id, expected_bids, data, ttl_left + 60)
        if not saved:
            # Пока ждали БД, лот перебили или завершили (лок на 5 с мог истечь).
            # Откатываем списание и возврат: деньги двигаются только вместе с состоянием лота.
            await cb.answer(t["lot_outbid"], show_alert=True)
            raise AbortUpdate

        # Обновляем сообщение
        new_text = _lot_text(data, p, t)
        try:
            await message.edit_text(
                new_text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
                reply_markup=_bid_kb(lot_id, cfg.bid_steps, p),
            )
        except TelegramBadRequest:
            pass

        await cb.answer(t["lot_your_bid"].format(price=new_price, score_word=p.pluralize(new_price)))

    finally:
        await store.lot_lock_release(chat_id, lot_id)


# ── Публичная функция завершения (используется loop'ом) ───────────────────


async def finish_lot(
    bot,
    chat_id: int,
    lot_id: str,
    store: RedisStore,
    p: ScorePluralizer,
    t: dict[str, str],
    delete_delay: int = 120,
) -> None:
    """Завершает аукцион: редактирует сообщение с итогом, анпинит, планирует удаление."""
    data = await store.lot_game_get(chat_id, lot_id)
    if data is None:
        return

    # Атомарно удаляем
    if not await store.lot_game_delete(chat_id, lot_id):
        return
    await store.lot_active_delete(chat_id)
    msg_id = data.get("message_id", 0)

    # Анпиним
    if msg_id:
        try:
            await bot.unpin_chat_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

    winner_id = data["leader_id"]
    description = html.escape(data["description"])

    if winner_id:
        prize = data["current_price"]
        result_text = t["lot_won"].format(
            description=description, winner=data["leader_display"], price=prize, score_word=p.pluralize(prize)
        )
        logger.info("lot: лот %s в чате %d выиграл user %d за %d", lot_id, chat_id, winner_id, prize)
    else:
        result_text = t["lot_no_bids_result"].format(description=description)
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
